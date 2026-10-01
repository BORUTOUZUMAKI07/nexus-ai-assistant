"""
Provider-based OAuth adapters (Authorization Code + PKCE, RFC 6749/7636).

Two first-class providers, mirroring the url-shortner SSO design:

  * **Google** — OIDC; the userinfo ``email_verified`` claim is the gate.
  * **GitHub** — plain OAuth2; it has no trustable userinfo claim, so a
    verified address (primary preferred) is re-fetched from ``/user/emails``
    instead of trusting the user-supplied ``/user.email`` (the account-takeover
    guard).

Each adapter is infrastructure-only: it builds the authorize URL, performs the
token exchange and fetches the identity. It touches no ORM and no database —
identity resolution and token issuance on the app side live on
``AuthService.sso_login`` (SRP).

``OAuthService`` orchestrates one provider: it mints the PKCE code_verifier,
persists the one-time ``state -> code_verifier`` mapping via an injectable
state store, and strictly validates the fetched identity (subject + verified
email) before returning an :class:`OAuthIdentity`. Every external touchpoint is
a seam for tests:

  * ``state_store``      — where the one-time ``state -> code_verifier`` mapping
    lives (default: the Redis-backed ``ICacheService``; tests inject memory).
  * ``code_exchanger``   — async ``(code, code_verifier) -> token dict``
    (default: the provider's own exchange; tests inject stubs).
  * ``userinfo_fetcher`` — async ``(token dict) -> userinfo dict``
    (default: the provider's own fetch; tests inject stubs).

State is strictly one-time use: a replayed or unknown ``state`` is rejected.
"""
import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Protocol
from urllib.parse import urlencode

import httpx
import structlog
from backend.app.core.config import settings
from backend.app.core.exceptions import AuthenticationError
from backend.app.infrastructure.cache.redis_client import get_cache_service

logger = structlog.get_logger(__name__)

# Defined but intentionally not exported — only the classes are used publicly.
CodeExchanger = Callable[[str, str], Awaitable[dict[str, Any]]]
UserinfoFetcher = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]
_HTTP_TIMEOUT = 10.0


def _claim_is_true(value: Any) -> bool:
    """Strictly interpret an OAuth boolean claim.

    Only an explicit affirmative counts. ``None``/missing, ``False``, ``0`` and
    the empty string are all treated as "not verified" — the safe default for a
    claim whose absence must never be read as consent. Some providers serialise
    booleans as the strings ``"true"``/``"false"``, so the exact string ``"true"``
    (any case) is honoured as well.
    """
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _select_verified_email(emails: list[dict[str, Any]]) -> str | None:
    """The only GitHub address worth trusting: a ``verified`` one.

    ``/user.email`` is a user-supplied, often public, *unverified* address;
    ``/user/emails`` carries ``verified`` flags. Accepting an unverified address
    hands a fully trusted account to whoever controls an arbitrary mailbox, so
    only an explicitly ``verified`` entry is ever returned — the verified
    primary when present, otherwise the first verified address.
    """
    fallback: str | None = None
    for entry in emails:
        if not _claim_is_true(entry.get("verified")):
            continue
        address = str(entry.get("email") or "").strip()
        if not address:
            continue
        if _claim_is_true(entry.get("primary")):
            return address
        if fallback is None:
            fallback = address
    return fallback


def _normalize_google_userinfo(data: dict[str, Any]) -> dict[str, Any]:
    """Normalise a Google userinfo response to the OIDC-shaped fields we gate on.

    Google has two userinfo shapes: the v2 endpoint returns ``id`` +
    ``verified_email``, while the OIDC endpoint returns ``sub`` +
    ``email_verified``. The identity gate in ``complete_login`` reads the
    OIDC names, so both shapes are mapped here — otherwise the v2 endpoint's
    response would yield an empty subject and fail every login.
    """
    return {
        "sub": str(data.get("sub") or data.get("id") or "").strip(),
        "email": data.get("email"),
        "email_verified": data.get("email_verified", data.get("verified_email", False)),
        "name": data.get("name"),
        "picture": data.get("picture"),
    }


@dataclass(frozen=True)
class OAuthIdentity:
    """Verified identity lifted from a provider userinfo response."""

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


class OAuthProvider(Protocol):
    """Transport contract every provider adapter must satisfy."""

    name: str

    def is_configured(self) -> bool: ...
    def build_authorization_url(self, state: str, code_challenge: str) -> str: ...
    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]: ...
    async def fetch_userinfo(self, token: dict[str, Any]) -> dict[str, Any]: ...


class GoogleOAuthProvider:
    """Google OAuth2/OIDC adapter (``email_verified`` claim gates the login)."""

    name = "google"
    AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
    TOKEN_URL = "https://oauth2.googleapis.com/token"  # nosec B105 — a URL, not a secret
    USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"
    SCOPE = "openid email profile"

    def __init__(self) -> None:
        self._client_id = settings.GOOGLE_OAUTH_CLIENT_ID
        self._client_secret = settings.GOOGLE_OAUTH_CLIENT_SECRET
        self._redirect_uri = settings.GOOGLE_OAUTH_REDIRECT_URI

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self.SCOPE,
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
            "prompt": "consent select_account",
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "grant_type": "authorization_code",
            "redirect_uri": self._redirect_uri,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(self.TOKEN_URL, data=data, timeout=_HTTP_TIMEOUT)
            resp.raise_for_status()
            return dict(resp.json())

    async def fetch_userinfo(self, token: dict[str, Any]) -> dict[str, Any]:
        access_token = _bearer_token(token)
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.USERINFO_URL,
                headers={"Authorization": f"Bearer {access_token}"},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            data = dict(resp.json())
        return _normalize_google_userinfo(data)


class GitHubOAuthProvider:
    """GitHub OAuth2 adapter — no OIDC claims, so /user/emails gates the login."""

    name = "github"
    AUTH_URL = "https://github.com/login/oauth/authorize"
    TOKEN_URL = "https://github.com/login/oauth/access_token"  # nosec B105 — a URL, not a secret
    USERINFO_URL = "https://api.github.com/user"
    EMAILS_URL = "https://api.github.com/user/emails"
    SCOPE = "read:user user:email"

    def __init__(self) -> None:
        self._client_id = settings.GITHUB_OAUTH_CLIENT_ID
        self._client_secret = settings.GITHUB_OAUTH_CLIENT_SECRET
        self._redirect_uri = settings.GITHUB_OAUTH_REDIRECT_URI

    def is_configured(self) -> bool:
        return bool(self._client_id and self._client_secret)

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "client_id": self._client_id,
            "redirect_uri": self._redirect_uri,
            "scope": self.SCOPE,
            "state": state,
            "prompt": "select_account",
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"{self.AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        data = {
            "client_id": self._client_id,
            "client_secret": self._client_secret,
            "code": code,
            "code_verifier": code_verifier,
        }
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                self.TOKEN_URL,
                data=data,
                headers={"Accept": "application/json"},
                timeout=_HTTP_TIMEOUT,
            )
            resp.raise_for_status()
            return dict(resp.json())

    async def fetch_userinfo(self, token: dict[str, Any]) -> dict[str, Any]:
        access_token = _bearer_token(token)
        headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.github+json"}
        async with httpx.AsyncClient() as client:
            user_resp = await client.get(self.USERINFO_URL, headers=headers, timeout=_HTTP_TIMEOUT)
            user_resp.raise_for_status()
            user = user_resp.json()
            emails = await self._fetch_email_entries(client, headers)

        # No OIDC claims on GitHub: /user's own `email` is user-supplied and often
        # unverified, so it is discarded entirely and only an address that
        # /user/emails marks `verified` is used. Absent one, email_verified is
        # False and the central gate in OAuthService.complete_login fails closed.
        verified_email = _select_verified_email(emails)
        return {
            "sub": str(user.get("id") or "").strip(),
            "email": verified_email,
            "email_verified": verified_email is not None,
            "name": user.get("name") or user.get("login"),
            "picture": user.get("avatar_url"),
        }

    async def _fetch_email_entries(
        self, client: httpx.AsyncClient, headers: dict[str, str]
    ) -> list[dict[str, Any]]:
        """Read ``/user/emails``, degrading to "no verified address" on any failure.

        A non-200 here usually means the OAuth App was created without the
        ``user:email`` scope, and a transport blip means we simply could not ask.
        Neither may be reported as "the provider is unreachable" (misleading) nor
        resolved by falling back to /user's unverified address (the
        account-takeover hole this endpoint exists to close), so both yield an
        empty list and the login fails closed on the verification gate.
        """
        try:
            resp = await client.get(self.EMAILS_URL, headers=headers, timeout=_HTTP_TIMEOUT)
            if resp.status_code != 200:
                logger.warning("github_emails_unavailable", status=resp.status_code)
                return []
            payload = resp.json()
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning("github_emails_unreachable", error=str(exc))
            return []
        return payload if isinstance(payload, list) else []


def _bearer_token(token: dict[str, Any]) -> str:
    access_token = token.get("access_token")
    if not access_token:
        raise AuthenticationError("Provider token exchange returned no access token.")
    return str(access_token)


class SSOProviderRegistry:
    """Name -> provider adapter registry (extensible for future IdPs)."""

    providers: dict[str, type] = {}

    @classmethod
    def register(cls, name: str, provider_cls: type) -> None:
        cls.providers[name] = provider_cls

    @classmethod
    def get(cls, name: str) -> OAuthProvider | None:
        provider_cls = cls.providers.get(name)
        if provider_cls is None:
            return None
        return provider_cls()  # type: ignore[no-any-return]

    @classmethod
    def list_providers(cls) -> list[str]:
        return list(cls.providers.keys())

    @classmethod
    def describe_providers(cls) -> list[dict[str, Any]]:
        """Every registered provider with its configuration state.

        Kept on the registry rather than in the router so the "is it usable
        here" rule lives next to the ``is_configured`` implementations it reads,
        and adding a third IdP needs no change to the transport layer.

        ``is_configured`` reads settings, so a provider instantiated here
        reflects this process's environment. That is the point: the sign-in page
        must reflect the server it is talking to, not a baked-in assumption.
        """
        described: list[dict[str, Any]] = []
        for name in cls.providers:
            provider = cls.get(name)
            described.append(
                {
                    "name": name,
                    "configured": bool(provider and provider.is_configured()),
                }
            )
        return described


SSOProviderRegistry.register("google", GoogleOAuthProvider)
SSOProviderRegistry.register("github", GitHubOAuthProvider)


class _UnconfiguredProvider:
    """Placeholder for an unknown/unregistered provider name.

    Never configured, so every route through it surfaces the usual 404. Its
    transport methods must never run (``enabled`` gates them) but exist so the
    class satisfies the :class:`OAuthProvider` shape.
    """

    def __init__(self, name: str) -> None:
        self._name = name

    @property
    def name(self) -> str:
        return self._name

    def is_configured(self) -> bool:
        return False

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        raise AuthenticationError("OAuth single sign-on is not configured.")

    async def exchange_code(self, code: str, code_verifier: str) -> dict[str, Any]:
        raise AuthenticationError("OAuth single sign-on is not configured.")

    async def fetch_userinfo(self, token: dict[str, Any]) -> dict[str, Any]:
        raise AuthenticationError("OAuth single sign-on is not configured.")


class OAuthService:
    """Orchestrates one provider: builds the PKCE authorize URL and completes login."""

    def __init__(
        self,
        *,
        provider: OAuthProvider,
        state_store: OAuthStateStore | None = None,
        state_ttl_seconds: int = 600,
        code_exchanger: CodeExchanger | None = None,
        userinfo_fetcher: UserinfoFetcher | None = None,
    ) -> None:
        self._provider = provider
        self._state_ttl_seconds = state_ttl_seconds
        self._state_store = state_store or _CacheOAuthStateStore(state_ttl_seconds)
        self._code_exchanger = code_exchanger or provider.exchange_code
        self._userinfo_fetcher = userinfo_fetcher or provider.fetch_userinfo

    @classmethod
    def for_provider(cls, provider_name: str) -> "OAuthService":
        """Build the adapter for a registered provider, or a never-enabled stub."""
        provider = SSOProviderRegistry.get(provider_name)
        if provider is None:
            provider = _UnconfiguredProvider(provider_name)
        return cls(provider=provider)

    @property
    def enabled(self) -> bool:
        return self._provider.is_configured()

    @property
    def provider(self) -> str:
        return self._provider.name

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

        authorization_url = self._provider.build_authorization_url(state, challenge)
        await self._state_store.set(state, code_verifier, ttl_seconds=self._state_ttl_seconds)
        logger.info(
            "oauth_authorization_url_created",
            provider=self._provider.name,
            state_len=len(state),
        )
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

        # A provider outage, DNS failure, non-2xx or non-JSON body is an upstream
        # fault, not a malformed user request. The adapters call raise_for_status
        # and let httpx raise; translate it into the typed AuthenticationError the
        # callback route maps to a clean 400 instead of letting it escape as an
        # unhandled 500. (ValueError covers json.JSONDecodeError.)
        try:
            token = await self._code_exchanger(code, code_verifier)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "oauth_token_exchange_failed",
                provider=self._provider.name,
                error=str(exc),
            )
            raise AuthenticationError("The identity provider could not be reached.") from exc

        try:
            userinfo = await self._userinfo_fetcher(token)
        except (httpx.HTTPError, ValueError) as exc:
            logger.warning(
                "oauth_userinfo_fetch_failed",
                provider=self._provider.name,
                error=str(exc),
            )
            raise AuthenticationError("The identity provider could not be reached.") from exc

        subject = str(userinfo.get("sub") or "").strip()
        email = str(userinfo.get("email") or "").lower().strip()
        if not subject or not email:
            raise AuthenticationError("Provider did not return a usable identity.")
        if not _claim_is_true(userinfo.get("email_verified")):
            # Fail closed on anything other than an explicit affirmative.
            # Accepting a *missing* email_verified claim (as this did before)
            # hands a fully trusted account to any provider that simply omits
            # the field — GitHub's raw /user response is a live example — and
            # the GitHub adapter deliberately synthesises email_verified only
            # from an address fetched from /user/emails that is verified. An
            # unverified or silent provider must fail the login, not provision
            # a user.
            raise AuthenticationError("Provider has not verified this email.")

        logger.info(
            "oauth_identity_verified",
            provider=self._provider.name,
            email_verified=True,
        )
        return OAuthIdentity(
            provider=self._provider.name,
            subject=subject,
            email=email,
            email_verified=True,
            name=userinfo.get("name") or userinfo.get("preferred_username"),
            picture=userinfo.get("picture"),
        )
