"""
OIDC SSO application-layer adapter (Authorization Code + PKCE, RFC 6749/7636).

Infrastructure-only: this module owns the *exchange* between Nexus and an
OpenID Connect provider. It touches no ORM and no database — identity
resolution and token issuance on the app side live on ``AuthService.sso_login``
(SRP). Authlib's ``AsyncOAuth2Client`` performs the token exchange and the
userinfo fetch; the PKCE code-verifier produced here is persisted by an
injectable state store until the callback arrives.

Every external touchpoint is a seam for tests:

  * ``state_store``  — where the one-time ``state -> code_verifier`` mapping
    lives (default: the Redis-backed ``ICacheService``; tests inject memory).
  * ``code_exchanger``   — async ``(code, code_verifier) -> token dict``
    (default: authlib token POST; tests inject stubs).
  * ``userinfo_fetcher`` — async ``(token dict) -> userinfo dict``
    (default: authlib GET userinfo with the bearer token; tests inject stubs).

State is strictly one-time use: a replayed or unknown ``state`` is rejected.
"""
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlencode

import structlog
from authlib.integrations.httpx_client import AsyncOAuth2Client
from backend.app.core.config import settings
from backend.app.core.exceptions import AuthenticationError
from backend.app.infrastructure.cache.redis_client import get_cache_service

logger = structlog.get_logger(__name__)

# Defined but intentionally not exported — only the class is used publicly.
CodeExchanger = Callable[[str, str], Awaitable[dict[str, Any]]]
UserinfoFetcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass(frozen=True)
class OAuthIdentity:
    """Verified identity lifted from an OIDC userinfo response."""

    provider: str
    subject: str
    email: str
    email_verified: bool = False
    name: str | None = None
    picture: str | None = None


class OAuthStateStore(Protocol):
    """One-time key/value store for PKCE state codes."""

    async def set(self, state: str, code_verifier: str, ttl_seconds: int | None = None) -> None: ...
    async def pop(self, state: str) -> str | None: ...


class _CacheOAuthStateStore:
    """Redis-backed state store via the app's ICacheService (injectable)."""

    def __init__(self, ttl_seconds: int | None = None) -> None:
        self._ttl_seconds = ttl_seconds

    async def set(self, state: str, code_verifier: str, ttl_seconds: int | None = None) -> None:
        await get_cache_service().set(
            f"oauth:state:{state}",
            code_verifier,
            ttl_seconds=ttl_seconds or self._ttl_seconds,
        )

    async def pop(self, state: str) -> str | None:
        cache = get_cache_service()
        code_verifier = await cache.get(f"oauth:state:{state}")
        if code_verifier is None:
            return None
        await cache.delete(f"oauth:state:{state}")
        return code_verifier


class OAuthService:
    """OIDC client adapter: builds the PKCE authorize URL and completes login."""

    def __init__(
        self,
        *,
        client_id: str | None,
        client_secret: str | None,
        authorize_url: str | None,
        token_url: str | None,
        userinfo_url: str | None,
        scope: str,
        redirect_uri: str,
        provider: str = "oidc",
        state_store: OAuthStateStore | None = None,
        state_ttl_seconds: int = 600,
        code_exchanger: CodeExchanger | None = None,
        userinfo_fetcher: UserinfoFetcher | None = None,
    ) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._authorize_url = authorize_url
        self._token_url = token_url
        self._userinfo_url = userinfo_url
        self._scope = scope
        self._redirect_uri = redirect_uri
        self._provider = provider
        self._state_ttl_seconds = state_ttl_seconds
        self._state_store = state_store or _CacheOAuthStateStore(state_ttl_seconds)
        self._code_exchanger = code_exchanger or self._default_code_exchange
        self._userinfo_fetcher = userinfo_fetcher or self._default_userinfo_fetch

    @classmethod
    def from_settings(cls) -> "OAuthService":
        return cls(
            client_id=settings.OAUTH_CLIENT_ID,
            client_secret=settings.OAUTH_CLIENT_SECRET,
            authorize_url=settings.OAUTH_AUTHORIZE_URL,
            token_url=settings.OAUTH_TOKEN_URL,
            userinfo_url=settings.OAUTH_USERINFO_URL,
            scope=settings.OAUTH_SCOPE,
            redirect_uri=(
                f"{settings.OAUTH_BACKEND_URL}{settings.API_V1_PREFIX}/auth/oauth/callback"
            ),
            state_ttl_seconds=settings.OAUTH_STATE_TTL_SECONDS,
        )

    @property
    def enabled(self) -> bool:
        return bool(
            self._client_id
            and self._authorize_url
            and self._token_url
            and self._userinfo_url
        )

    @property
    def provider(self) -> str:
        return self._provider

    async def create_authorization_url(self) -> tuple[str, str]:
        """Build the provider authorize URL (S256 PKCE) and persist the state.

        Returns ``(authorization_url, state)``. The state's code_verifier is
        stored one-time and consumed when the callback arrives.
        """
        if not self.enabled:
            raise AuthenticationError("OAuth single sign-on is not configured.")

        # RFC 7636: a fresh 96-bit random code_verifier and its S256 challenge.
        code_verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        state = secrets.token_urlsafe(24)

        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self._scope,
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        authorization_url = f"{self._authorize_url}?{urlencode(params)}"
        await self._state_store.set(state, code_verifier, ttl_seconds=self._state_ttl_seconds)
        logger.info("oauth_authorization_url_created", state_len=len(state))
        return authorization_url, state

    async def complete_login(self, code: str, state: str) -> OAuthIdentity:
        """Exchange ``code`` for tokens, fetch userinfo, build a verified identity.

        Raises ``AuthenticationError`` for unknown/expired state, missing or
        explicitly-unverified emails, or exchange failures.
        """
        if not self.enabled:
            raise AuthenticationError("OAuth single sign-on is not configured.")
        if not code or not state:
            raise AuthenticationError("OAuth callback is missing code or state.")

        # One-time state: pop deletes the mapping, so a replay of `state` fails.
        code_verifier = await self._state_store.pop(state)
        if not code_verifier:
            raise AuthenticationError("Invalid or expired OAuth state.")

        token = await self._code_exchanger(code, code_verifier)
        userinfo = await self._userinfo_fetcher(token)

        subject = str(userinfo.get("sub") or "").strip()
        email = str(userinfo.get("email") or "").lower().strip()
        if not subject or not email:
            raise AuthenticationError("Provider did not return a usable identity.")
        if userinfo.get("email_verified") is False:
            # Never auto-create an account for a provider that could not verify
            # the email (account-takeover prevention).
            raise AuthenticationError("Provider has not verified this email.")

        logger.info(
            "oauth_identity_verified",
            provider=self._provider,
            email_verified=bool(userinfo.get("email_verified")),
        )
        return OAuthIdentity(
            provider=self._provider,
            subject=subject,
            email=email,
            email_verified=bool(userinfo.get("email_verified")),
            name=userinfo.get("name") or userinfo.get("preferred_username"),
            picture=userinfo.get("picture"),
        )

    # ── default transports (authlib) ──────────────────────────────────────────

    async def _default_code_exchange(self, code: str, code_verifier: str) -> dict[str, Any]:
        client = AsyncOAuth2Client(
            client_id=self._client_id,
            client_secret=self._client_secret,
            redirect_uri=self._redirect_uri,
        )
        token = await client.fetch_token(
            self._token_url,
            grant_type="authorization_code",
            code=code,
            code_verifier=code_verifier,
        )
        return dict(token)

    async def _default_userinfo_fetch(self, token: dict[str, Any]) -> dict[str, Any]:
        client = AsyncOAuth2Client(token=token)
        resp = await client.get(self._userinfo_url)
        resp.raise_for_status()
        return resp.json()
