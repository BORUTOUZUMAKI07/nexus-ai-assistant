"""
Tests for provider-based OAuth (Authorization Code + PKCE).

Covers, all offline (the provider never hears from these tests):

  * OAuthService PKCE authorize-URL construction (RFC 7636 S256 challenge
    round-trips against the stored code_verifier) and enabled-gating.
  * complete_login: code -> token -> userinfo -> OAuthIdentity, with strict
    one-time-use state, and rejection of unknown state / missing / unverified
    email (account-takeover prevention).
  * The provider registry: google + github registered, unknown names disabled.
  * Google/GitHub provider authorize-URL builds (scopes, redirect URIs, PKCE
    params) and GitHub's verified-primary-email selection.
  * AuthService.oauth_login: find-or-provision by email, verified-lift, inactive
    rejection, TOTP preauth gating, collision-safe username allocation.
  * /auth/oauth/{provider} + /auth/oauth/{provider}/callback routes: 404 while
    disabled, and the full happy path wiring through the DI override seam.
  * GET /auth/oauth/providers: which providers this deployment can use, and the
    route-ordering fact that keeps it from being swallowed by the {provider}
    catch-all declared after it.
"""
import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlencode, urlparse
from uuid import uuid4

import httpx
import pytest
from backend.app.api import deps
from backend.app.core.config import settings
from backend.app.core.exceptions import AuthenticationError
from backend.app.domain.user.models import User, UserSettings
from backend.app.domain.user.schemas import TokenResponse, TwoFactorChallengeResponse
from backend.app.main import app
from backend.app.services import oauth_service as oauth_module
from backend.app.services.auth_service import AuthService
from backend.app.services.oauth_service import (
    GitHubOAuthProvider,
    GoogleOAuthProvider,
    OAuthProviderRegistry,
    OAuthService,
    _normalize_google_userinfo,
    _select_verified_email,
)
from backend.tests.fakes import FakeSession
from httpx import ASGITransport, AsyncClient

# ─── fakes ───────────────────────────────────────────────────────────────

class MemoryStateStore:
    """In-process one-time PKCE state store (mirrors the Redis-backed one)."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}

    async def set(self, state: str, code_verifier: str, ttl_seconds: int | None = None) -> None:
        self._data[state] = code_verifier

    async def pop(self, state: str) -> str | None:
        return self._data.pop(state, None)


async def _fake_exchange(code: str, code_verifier: str) -> dict:
    return {"access_token": "oauth-token", "token_type": "Bearer"}


async def _fake_userinfo(token: dict) -> dict:
    return {
        "sub": "idp-subject-123",
        "email": "oauth@nexus.ai",
        "email_verified": True,
        "name": "OAuth User",
        "picture": "https://cdn.example/avatar.png",
    }


class _TestProvider:
    """Offline stand-in provider: Google-shaped userinfo, configurable gating."""

    name = "oidc"

    def __init__(self, enabled: bool = True) -> None:
        self._enabled = enabled

    def is_configured(self) -> bool:
        return self._enabled

    def build_authorization_url(self, state: str, code_challenge: str) -> str:
        params = {
            "response_type": "code",
            "client_id": "test-client",
            "redirect_uri": "http://localhost:8000/api/v1/auth/oauth/callback",
            "scope": "openid profile email",
            "state": state,
            "code_challenge": code_challenge,
            "code_challenge_method": "S256",
        }
        return f"https://idp.example/authorize?{urlencode(params)}"

    async def exchange_code(self, code: str, code_verifier: str) -> dict:
        return _fake_exchange(code, code_verifier)

    async def fetch_userinfo(self, token: dict) -> dict:
        return _fake_userinfo(token)


def _make_service(
    *,
    store: MemoryStateStore | None = None,
    exchanger=None,
    fetcher=None,
    enabled: bool = True,
) -> OAuthService:
    return OAuthService(
        provider=_TestProvider(enabled=enabled),
        state_store=store or MemoryStateStore(),
        code_exchanger=exchanger or _fake_exchange,
        userinfo_fetcher=fetcher or _fake_userinfo,
    )


def _await(coro):
    # These tests are sync but drive coroutines directly. Once pytest-asyncio has
    # run an async test it tears the current loop down, so get_event_loop()
    # raises in later sync tests. Re-establish one rather than depending on
    # collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


# ─── Provider registry ────────────────────────────────────────────────────

def test_registry_registers_google_and_github():
    assert set(OAuthProviderRegistry.list_providers()) == {"google", "github"}
    assert OAuthProviderRegistry.get("google") is not None
    assert OAuthProviderRegistry.get("github") is not None


def test_for_provider_unknown_name_is_never_enabled():
    svc = OAuthService.for_provider("keycloak")
    assert svc.enabled is False
    assert svc.provider == "keycloak"
    with pytest.raises(AuthenticationError):
        _await(svc.create_authorization_url())
    with pytest.raises(AuthenticationError):
        _await(svc.complete_login("code", "state"))


def test_provider_configured_flags_follow_client_ids(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", "gh-client")
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "gh-secret")
    assert GoogleOAuthProvider().is_configured() is False
    assert GitHubOAuthProvider().is_configured() is True


# ─── Provider authorize-URL builds (offline) ──────────────────────────────

def test_google_provider_builds_authorize_url(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "g-client")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "g-secret")
    monkeypatch.setattr(
        settings, "GOOGLE_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v1/auth/oauth/google/callback"
    )
    provider = GoogleOAuthProvider()

    url = provider.build_authorization_url("st", "ch")

    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["g-client"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/google/callback"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge"] == ["ch"]
    assert query["code_challenge_method"] == ["S256"]


def test_github_provider_builds_authorize_url(monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", "gh-client")
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "gh-secret")
    monkeypatch.setattr(
        settings, "GITHUB_OAUTH_REDIRECT_URI", "http://localhost:8000/api/v1/auth/oauth/github/callback"
    )
    provider = GitHubOAuthProvider()

    url = provider.build_authorization_url("st", "ch")

    assert url.startswith("https://github.com/login/oauth/authorize?")
    query = parse_qs(urlparse(url).query)
    assert query["client_id"] == ["gh-client"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/github/callback"]
    assert query["scope"] == ["read:user user:email"]
    assert query["code_challenge_method"] == ["S256"]


# ─── GitHub verified-email selection ──────────────────────────────────────

def test_select_verified_email_prefers_verified_primary():
    emails = [
        {"email": "public@example.com", "primary": False, "verified": True},
        {"email": "primary@example.com", "primary": True, "verified": True},
    ]
    assert _select_verified_email(emails) == "primary@example.com"


def test_select_verified_email_falls_back_to_any_verified():
    """No verified primary: the first verified address is still trustworthy."""
    emails = [
        {"email": "unverified-primary@example.com", "primary": True, "verified": False},
        {"email": "verified-secondary@example.com", "primary": False, "verified": True},
    ]
    assert _select_verified_email(emails) == "verified-secondary@example.com"


def test_select_verified_email_rejects_unverified_only():
    emails = [
        {"email": "spoof@example.com", "primary": True, "verified": False},
        {"email": "other@example.com", "primary": False, "verified": False},
    ]
    assert _select_verified_email(emails) is None


def test_select_verified_email_tolerates_missing_fields():
    assert _select_verified_email([{}]) is None
    assert _select_verified_email([]) is None


# ─── Google userinfo normalisation ────────────────────────────────────────

def test_normalize_google_userinfo_maps_v2_fields():
    """The v2 endpoint returns ``id``/``verified_email``; the gate reads OIDC names."""
    normalized = _normalize_google_userinfo(
        {"id": 123, "email": "g@nexus.ai", "verified_email": True, "name": "G"}
    )
    assert normalized["sub"] == "123"
    assert normalized["email"] == "g@nexus.ai"
    assert normalized["email_verified"] is True


def test_normalize_google_userinfo_maps_oidc_fields():
    normalized = _normalize_google_userinfo(
        {"sub": "456", "email": "g@nexus.ai", "email_verified": True}
    )
    assert normalized["sub"] == "456"
    assert normalized["email_verified"] is True


def test_normalize_google_userinfo_missing_verification_is_false():
    normalized = _normalize_google_userinfo({"id": "789", "email": "g@nexus.ai"})
    assert normalized["email_verified"] is False


# ─── provider adapters: real HTTP paths (httpx stubbed) ───────────────────

class _FakeResponse:
    """Minimal stand-in for httpx.Response for the adapter paths."""

    def __init__(self, status_code: int = 200, payload: object = None) -> None:
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self) -> object:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}",
                request=httpx.Request("GET", "https://provider.test"),
                response=httpx.Response(self.status_code),
            )


class _FakeAsyncClient:
    """Routes adapter calls by URL; a value may be a response or an exception."""

    def __init__(self, routes: dict) -> None:
        self._routes = routes
        self.requests: list[dict] = []

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, *exc_info: object) -> bool:
        return False

    async def get(self, url: str, headers=None, timeout=None):
        self.requests.append({"method": "GET", "url": url, "headers": headers})
        result = self._routes[url]
        if isinstance(result, Exception):
            raise result
        return result

    async def post(self, url: str, data=None, headers=None, timeout=None):
        self.requests.append({"method": "POST", "url": url, "data": data, "headers": headers})
        result = self._routes[url]
        if isinstance(result, Exception):
            raise result
        return result


@pytest.fixture
def fake_http(monkeypatch):
    """Install a fake httpx.AsyncClient whose routing table the test controls."""
    def _install(routes: dict) -> _FakeAsyncClient:
        client = _FakeAsyncClient(routes)
        monkeypatch.setattr(oauth_module.httpx, "AsyncClient", lambda *a, **kw: client)
        return client

    return _install


GH = GitHubOAuthProvider
TOKEN = {"access_token": "gho_test"}


def test_github_fetch_userinfo_uses_verified_email(fake_http):
    fake_http(
        {
            GH.USERINFO_URL: _FakeResponse(200, {"id": 42, "login": "octo", "email": "public@example.com"}),
            GH.EMAILS_URL: _FakeResponse(
                200,
                [
                    {"email": "public@example.com", "primary": False, "verified": True},
                    {"email": "real@example.com", "primary": True, "verified": True},
                ],
            ),
        }
    )

    info = _await(GH().fetch_userinfo(TOKEN))

    assert info["sub"] == "42"
    assert info["email"] == "real@example.com"
    assert info["email_verified"] is True
    assert info["name"] == "octo"  # falls back to login when `name` is absent


def test_github_fetch_userinfo_never_trusts_the_public_email(fake_http):
    """The account-takeover guard: an unverified address must never be lifted."""
    fake_http(
        {
            GH.USERINFO_URL: _FakeResponse(200, {"id": 7, "email": "victim@example.com"}),
            GH.EMAILS_URL: _FakeResponse(
                200, [{"email": "victim@example.com", "primary": True, "verified": False}]
            ),
        }
    )

    info = _await(GH().fetch_userinfo(TOKEN))

    assert info["email"] is None
    assert info["email_verified"] is False


def test_github_fetch_userinfo_fails_closed_when_email_scope_missing(fake_http):
    """A 404 on /user/emails (app created without user:email) must not raise."""
    fake_http(
        {
            GH.USERINFO_URL: _FakeResponse(200, {"id": 7, "email": "public@example.com"}),
            GH.EMAILS_URL: _FakeResponse(404, {"message": "Not Found"}),
        }
    )

    info = _await(GH().fetch_userinfo(TOKEN))

    assert info["email"] is None
    assert info["email_verified"] is False


def test_github_fetch_userinfo_fails_closed_when_emails_unreachable(fake_http):
    fake_http(
        {
            GH.USERINFO_URL: _FakeResponse(200, {"id": 7}),
            GH.EMAILS_URL: httpx.ConnectError("connection reset"),
        }
    )

    info = _await(GH().fetch_userinfo(TOKEN))

    assert info["email"] is None
    assert info["email_verified"] is False


def test_github_exchange_code_posts_expected_form(fake_http, monkeypatch):
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", "gh-client")
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", "gh-secret")
    monkeypatch.setattr(
        settings, "GITHUB_OAUTH_REDIRECT_URI", "https://app.test/api/auth/oauth/github/callback"
    )
    client = fake_http({GH.TOKEN_URL: _FakeResponse(200, {"access_token": "gho_new"})})

    token = _await(GH().exchange_code("auth-code", "verifier-123"))

    assert token == {"access_token": "gho_new"}
    (sent,) = client.requests
    assert sent["url"] == GH.TOKEN_URL
    assert sent["data"] == {
        "client_id": "gh-client",
        "client_secret": "gh-secret",
        "code": "auth-code",
        "code_verifier": "verifier-123",
    }
    # Without this header GitHub answers with a URL-encoded body instead of JSON
    # and dict(resp.json()) blows up.
    assert sent["headers"]["Accept"] == "application/json"


def test_google_fetch_userinfo_normalizes_v2_shape(fake_http):
    """Google's v2 endpoint answers `id`/`verified_email`; the gate reads OIDC names."""
    fake_http(
        {
            GoogleOAuthProvider.USERINFO_URL: _FakeResponse(
                200, {"id": 99, "email": "g@nexus.ai", "verified_email": True, "name": "G"}
            )
        }
    )

    info = _await(GoogleOAuthProvider().fetch_userinfo(TOKEN))

    assert info["sub"] == "99"
    assert info["email"] == "g@nexus.ai"
    assert info["email_verified"] is True


def test_google_fetch_userinfo_does_not_invent_verification(fake_http):
    fake_http(
        {GoogleOAuthProvider.USERINFO_URL: _FakeResponse(200, {"id": 99, "email": "g@nexus.ai"})}
    )

    info = _await(GoogleOAuthProvider().fetch_userinfo(TOKEN))

    assert info["email_verified"] is False


# ─── OAuthService: PKCE authorize URL ─────────────────────────────────────

def test_enabled_false_when_not_configured():
    svc = _make_service(enabled=False)
    assert svc.enabled is False
    with pytest.raises(AuthenticationError):
        _await(svc.create_authorization_url())


def test_create_authorization_url_builds_pkce_request():
    store = MemoryStateStore()
    svc = _make_service(store=store)

    url, state = _await(svc.create_authorization_url())

    assert url.startswith("https://idp.example/authorize?")
    query = parse_qs(urlparse(url).query)
    assert query["response_type"] == ["code"]
    assert query["client_id"] == ["test-client"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/v1/auth/oauth/callback"]
    assert query["scope"] == ["openid profile email"]
    assert query["state"] == [state]  # the IdP must echo OUR state back
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["code_challenge"][0]) == 43  # base64url of 32 bytes

    # The S256 challenge must be exactly the hash of the verifier we stored —
    # any drift in the challenge math breaks every configured provider.
    verifier = _await(store.pop(state))
    assert verifier is not None
    assert _s256_challenge(verifier) == query["code_challenge"][0]


# ─── OAuthService: complete_login ─────────────────────────────────────────

def test_complete_login_exchanges_and_builds_identity():
    store = MemoryStateStore()
    svc = _make_service(store=store)
    _, state = _await(svc.create_authorization_url())

    identity = _await(svc.complete_login("auth-code-1", state))

    assert identity.provider == "oidc"
    assert identity.subject == "idp-subject-123"
    assert identity.email == "oauth@nexus.ai"
    assert identity.email_verified is True
    assert identity.name == "OAuth User"
    assert identity.picture == "https://cdn.example/avatar.png"
    # state is one-time use — the verifier is gone after the exchange
    assert _await(store.pop(state)) is None


def test_complete_login_rejects_unknown_state():
    svc = _make_service()
    with pytest.raises(AuthenticationError, match="state"):
        _await(svc.complete_login("auth-code-1", "never-issued"))


def test_complete_login_rejects_unverified_email():
    async def unverified(token):
        return {"sub": "s1", "email": "evil@nexus.ai", "email_verified": False}

    svc = _make_service(fetcher=unverified)
    _, state = _await(svc.create_authorization_url())
    with pytest.raises(AuthenticationError, match="not verified"):
        _await(svc.complete_login("auth-code-1", state))


def test_complete_login_rejects_missing_email():
    async def no_email(token):
        return {"sub": "s1"}

    svc = _make_service(fetcher=no_email)
    _, state = _await(svc.create_authorization_url())
    with pytest.raises(AuthenticationError, match="identity"):
        _await(svc.complete_login("auth-code-1", state))


def test_complete_login_uses_the_stored_verifier_for_exchange():
    captured = {}

    async def spy_exchange(code: str, code_verifier: str) -> dict:
        captured["code"] = code
        captured["verifier"] = code_verifier
        return {"access_token": "t", "token_type": "Bearer"}

    store = MemoryStateStore()
    svc = _make_service(store=store, exchanger=spy_exchange)
    _, state = _await(svc.create_authorization_url())
    stored_verifier = store._data[state]  # read before the one-time pop

    _await(svc.complete_login("the-code", state))

    assert captured == {"code": "the-code", "verifier": stored_verifier}


# ─── AuthService.oauth_login ────────────────────────────────────────────────

def _identity(**overrides):
    from backend.app.services.oauth_service import OAuthIdentity

    base = dict(
        provider="oidc",
        subject="idp-subject-123",
        email="oauth@nexus.ai",
        email_verified=True,
        name="OAuth User",
        picture="https://cdn.example/avatar.png",
    )
    base.update(overrides)
    return OAuthIdentity(**base)


def test_oauth_provisions_new_user_and_issues_tokens():
    fake = FakeSession()
    svc = AuthService(fake)

    result = _await(svc.oauth_login(_identity()))

    assert isinstance(result, TokenResponse)
    assert result.access_token and result.refresh_token
    rows = fake.rows.get(User, [])
    assert len(rows) == 1
    user = rows[0]
    assert user.email == "oauth@nexus.ai"
    assert user.username == "oauth"
    assert user.hashed_password is None  # passwordless OAuth account
    assert user.is_verified is True
    assert user.full_name == "OAuth User"
    assert fake.rows.get(UserSettings, [])  # default settings row created


def test_oauth_existing_user_is_verified_and_gets_tokens():
    fake = FakeSession()
    user = User(email="oauth@nexus.ai", username="oauth", is_verified=False)
    fake.seed(User, [user])
    svc = AuthService(fake)

    result = _await(svc.oauth_login(_identity()))

    assert isinstance(result, TokenResponse)
    assert user.is_verified is True
    assert len(fake.rows.get(User, [])) == 1  # no duplicate provisioning


def test_oauth_inactive_account_rejected():
    fake = FakeSession()
    fake.seed(User, [User(email="oauth@nexus.ai", username="oauth", is_active=False)])
    svc = AuthService(fake)

    with pytest.raises(AuthenticationError, match="inactive"):
        _await(svc.oauth_login(_identity()))


def test_oauth_totp_account_gets_preauth_challenge():
    fake = FakeSession()
    user = User(email="oauth@nexus.ai", username="oauth", is_verified=True)
    fake.seed(User, [user])
    fake.seed(
        UserSettings,
        [UserSettings(user_id=user.id, totp_secret="FAKESECRET",
                      custom_settings={"two_factor_enabled": True})],
    )
    svc = AuthService(fake)

    result = _await(svc.oauth_login(_identity()))

    assert isinstance(result, TwoFactorChallengeResponse)
    assert result.status == "2fa_required"
    assert result.preauth_token


def test_oauth_username_collision_gets_suffix():
    fake = FakeSession()
    fake.seed(User, [User(email="other@nexus.ai", username="oauth")])
    svc = AuthService(fake)

    result = _await(svc.oauth_login(_identity()))

    assert isinstance(result, TokenResponse)
    provisioned = [u for u in fake.rows.get(User, []) if u.email == "oauth@nexus.ai"]
    assert len(provisioned) == 1
    assert provisioned[0].username != "oauth"
    assert provisioned[0].username.startswith("oauth-")


def test_oauth_requires_subject():
    fake = FakeSession()
    svc = AuthService(fake)
    with pytest.raises(AuthenticationError, match="identity"):
        _await(svc.oauth_login(_identity(subject="")))


def test_complete_login_rejects_missing_email_verified_claim():
    """A provider that simply omits ``email_verified`` must be rejected.

    Accepting a missing claim silently grants full trust to any IdP that does
    not bother to assert verification — the exact shape of a raw GitHub /user
    response, where ``email_verified`` is absent while the address is
    user-supplied and unverified.
    """
    async def no_claim(token):
        return {"sub": "s1", "email": "unverified@nexus.ai"}  # no email_verified

    svc = _make_service(fetcher=no_claim)
    _, state = _await(svc.create_authorization_url())

    with pytest.raises(AuthenticationError, match="not verified"):
        _await(svc.complete_login("auth-code-1", state))


def test_complete_login_accepts_stringified_true_claim():
    """Some providers serialise booleans as strings; "true" still counts."""
    async def string_claim(token):
        return {"sub": "s1", "email": "ok@nexus.ai", "email_verified": "true"}

    svc = _make_service(fetcher=string_claim)
    _, state = _await(svc.create_authorization_url())

    identity = _await(svc.complete_login("auth-code-1", state))
    assert identity.email_verified is True


def test_complete_login_accepts_google_v2_shaped_userinfo():
    """Regression: the v2 userinfo endpoint returns ``id``/``verified_email``.

    The gate reads the OIDC names, so an unnormalised v2 response would give an
    empty subject and fail every Google login. The provider normalises it.
    """
    async def v2_fetcher(token):
        return _normalize_google_userinfo(
            {"id": 42, "email": "g@nexus.ai", "verified_email": True, "name": "G"}
        )

    svc = _make_service(fetcher=v2_fetcher)
    _, state = _await(svc.create_authorization_url())

    identity = _await(svc.complete_login("auth-code-1", state))
    assert identity.subject == "42"
    assert identity.email == "g@nexus.ai"
    assert identity.email_verified is True


def test_complete_login_wraps_token_exchange_transport_error():
    """A provider outage must surface as AuthenticationError, not a raw httpx error.

    The callback route only catches AuthenticationError, so an escaping
    httpx exception would become an unhandled 500 instead of a clean 400.
    """
    async def dead_exchanger(code, code_verifier):
        raise httpx.ConnectError("name resolution failed")

    svc = _make_service(exchanger=dead_exchanger)
    _, state = _await(svc.create_authorization_url())

    with pytest.raises(AuthenticationError, match="could not be reached"):
        _await(svc.complete_login("auth-code-1", state))


def test_complete_login_wraps_unparseable_userinfo():
    """A 200 with a non-JSON body is an upstream fault, not a user error."""
    async def bad_json_fetcher(token):
        raise ValueError("Expecting value: line 1 column 1 (char 0)")

    svc = _make_service(fetcher=bad_json_fetcher)
    _, state = _await(svc.create_authorization_url())

    with pytest.raises(AuthenticationError, match="could not be reached"):
        _await(svc.complete_login("auth-code-1", state))


def test_oauth_login_rejects_unverified_identity():
    """Defence in depth: oauth_login itself refuses an unverified identity.

    ``OAuthService`` already gates this, so the only way to get here is a caller
    that skipped that gate — the check must not be reachable-around.
    """
    fake = FakeSession()
    svc = AuthService(fake)
    with pytest.raises(AuthenticationError, match="not verified"):
        _await(svc.oauth_login(_identity(email_verified=False)))
    assert fake.rows.get(User, []) == []  # nothing provisioned


def test_oauth_binds_existing_account_to_provider_subject():
    """First OAuth login for a password account records the provider linkage."""
    fake = FakeSession()
    user = User(email="oauth@nexus.ai", username="oauth", is_verified=True)
    fake.seed(User, [user])
    svc = AuthService(fake)

    _await(svc.oauth_login(_identity()))

    assert user.oauth_provider == "oidc"
    assert user.oauth_sub == "idp-subject-123"
    assert len(fake.rows.get(User, [])) == 1  # adopted, not duplicated


def test_oauth_resolves_by_subject_when_provider_reassigns_email():
    """The subject id — not the email — is the authoritative join key.

    The IdP reports a *different* address for the same account (address
    recycled at the provider, or a corporate rename). Login must still resolve
    to the already-linked user, and must NOT provision a second account for the
    new address. Under the old email-only lookup this silently created a
    duplicate or hijacked whichever account owned that address.
    """
    fake = FakeSession()
    linked = User(
        email="old-address@nexus.ai",
        username="linked",
        is_verified=True,
        oauth_provider="oidc",
        oauth_sub="idp-subject-123",
    )
    fake.seed(User, [linked])
    svc = AuthService(fake)

    result = _await(svc.oauth_login(_identity(email="reassigned@nexus.ai")))

    assert isinstance(result, TokenResponse)
    # Exactly one account, and the linked one — no duplicate for the new email.
    assert len(fake.rows.get(User, [])) == 1
    assert linked.email == "old-address@nexus.ai"  # we never rewrite it


def test_oauth_new_account_stores_provider_identity():
    """A provisioned OAuth account records the subject that created it."""
    fake = FakeSession()
    svc = AuthService(fake)

    _await(svc.oauth_login(_identity()))

    user = fake.rows.get(User, [])[0]
    assert user.oauth_provider == "oidc"
    assert user.oauth_sub == "idp-subject-123"


def test_oauth_login_refuses_email_of_an_account_already_linked_elsewhere():
    """The email fallback must not hand over an account already bound to a
    different provider subject.

    Previously the link was skipped but the caller was still logged in as that
    account, so a recycled/reassigned address (or a second IdP sharing the
    email) inherited it -- the exact takeover the subject-first order aims to
    prevent. It must refuse instead.
    """
    fake = FakeSession()
    linked = User(
        email="oauth@nexus.ai",
        username="linked",
        is_verified=True,
        oauth_provider="other-idp",
        oauth_sub="a-different-subject",
    )
    fake.seed(User, [linked])
    svc = AuthService(fake)

    with pytest.raises(AuthenticationError, match="already linked"):
        _await(svc.oauth_login(_identity()))

    # The existing linkage is untouched and no duplicate account is created.
    assert linked.oauth_provider == "other-idp"
    assert linked.oauth_sub == "a-different-subject"
    assert len(fake.rows.get(User, [])) == 1


# ─── Routes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_login_route_404_when_disabled():
    app.dependency_overrides[deps.get_oauth_service] = lambda: _make_service(enabled=False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/oauth/google")
        assert resp.status_code == 404
        assert "not configured" in resp.json()["detail"]
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)


@pytest.mark.asyncio
async def test_oauth_login_route_404_for_unknown_provider():
    app.dependency_overrides[deps.get_oauth_service] = lambda: OAuthService.for_provider("bogus")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/oauth/bogus")
        assert resp.status_code == 404
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)


@pytest.mark.asyncio
async def test_oauth_login_route_returns_authorization_url():
    store = MemoryStateStore()
    svc = _make_service(store=store)
    app.dependency_overrides[deps.get_oauth_service] = lambda: svc
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/oauth/google")
        assert resp.status_code == 200
        body = resp.json()
        assert body["provider"] == "oidc"
        assert body["authorization_url"].startswith("https://idp.example/authorize?")
        query = parse_qs(urlparse(body["authorization_url"]).query)
        assert query["state"] == [body["state"]]
        assert query["client_id"] == ["test-client"]
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)


@pytest.mark.asyncio
async def test_oauth_callback_route_issues_tokens():
    store = MemoryStateStore()
    svc = _make_service(store=store)
    fake = FakeSession()
    auth_svc = AuthService(fake)

    app.dependency_overrides[deps.get_oauth_service] = lambda: svc
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_svc
    try:
        _, state = await svc.create_authorization_url()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.post(
                "/api/v1/auth/oauth/google/callback",
                json={"code": "auth-code-1", "state": state},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["access_token"]
        assert body["refresh_token"]
        # and the account was provisioned through the real service chain
        assert any(u.email == "oauth@nexus.ai" for u in fake.rows.get(User, []))
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)
        app.dependency_overrides.pop(deps.get_auth_service, None)


# ─── GET /auth/oauth/{provider}/callback (direct browser redirect) ────────
#
# This is the route an IdP redirect URI can point at, and it answers with a
# `Location` header rather than JSON. Two facts are load-bearing: the origin is
# `APP_PUBLIC_URL` (a deployed instance must not bounce every OAuth user to
# localhost:3000) and the error message is URL-encoded (a raw space in a
# `Location` is not a valid URI).

@pytest.mark.asyncio
async def test_browser_callback_uses_the_configured_public_origin(monkeypatch):
    monkeypatch.setattr(settings, "APP_PUBLIC_URL", "https://app.nexus.example/")
    svc = _make_service()
    app.dependency_overrides[deps.get_oauth_service] = lambda: svc
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/oauth/google/callback")
        assert resp.status_code == 302
        # Trailing slash on APP_PUBLIC_URL must not become a double slash.
        assert (
            resp.headers["location"]
            == "https://app.nexus.example/signin?error=oauth_missing_params"
        )
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)


@pytest.mark.asyncio
async def test_browser_callback_percent_encodes_query_significant_characters(monkeypatch):
    """Starlette encodes spaces in a ``Location`` header, but it treats ``&``,
    ``=``, ``#`` and ``?`` as safe. A provider error containing one of those
    would split or truncate the ``error=`` query, so the route must encode the
    message itself. A message with a space alone proves nothing (Starlette
    handles that); this one carries ``&``.
    """
    monkeypatch.setattr(settings, "APP_PUBLIC_URL", "https://app.nexus.example")

    class _RaisingService:
        enabled = True

        async def complete_login(self, code: str, state: str):
            raise AuthenticationError("idp said no & stop")

    app.dependency_overrides[deps.get_oauth_service] = lambda: _RaisingService()
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/auth/oauth/google/callback",
                params={"code": "c", "state": "s"},
            )
        assert resp.status_code == 302
        error_value = resp.headers["location"].split("error=", 1)[1]
        assert "&" not in error_value, f"raw `&` splits the query: {error_value!r}"
        assert "%26" in error_value
        assert "%20" in error_value
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)


@pytest.mark.asyncio
async def test_browser_callback_success_sets_session_cookies_and_goes_to_app(monkeypatch):
    monkeypatch.setattr(settings, "APP_PUBLIC_URL", "https://app.nexus.example")
    store = MemoryStateStore()
    svc = _make_service(store=store)
    auth_svc = AuthService(FakeSession())
    app.dependency_overrides[deps.get_oauth_service] = lambda: svc
    app.dependency_overrides[deps.get_auth_service] = lambda: auth_svc
    try:
        _, state = await svc.create_authorization_url()
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                "/api/v1/auth/oauth/google/callback",
                params={"code": "auth-code-1", "state": state},
            )
        assert resp.status_code == 302
        assert resp.headers["location"] == "https://app.nexus.example/app"
        cookies = resp.headers.get_list("set-cookie")
        assert any("nexus_access_token=" in c for c in cookies), cookies
        assert any("nexus_refresh_token=" in c for c in cookies), cookies
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)
        app.dependency_overrides.pop(deps.get_auth_service, None)


# ─── GET /auth/oauth/providers ────────────────────────────────────────────
#
# The sign-in page needs to know which OAuth buttons to render. A hardcoded list
# is wrong in both directions: a deployment with only GitHub configured still
# shows Google, and a deployment with neither still shows both -- and the user
# only discovers it after a round trip to the IdP.

@pytest.mark.asyncio
async def test_providers_route_lists_every_registered_provider():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/oauth/providers")
    assert resp.status_code == 200
    names = {p["name"] for p in resp.json()["providers"]}
    assert names == set(OAuthProviderRegistry.providers), (
        f"route reported {names} but the registry holds {set(OAuthProviderRegistry.providers)}"
    )
    assert {"google", "github"} <= names


@pytest.mark.asyncio
async def test_providers_route_needs_no_session():
    """Unauthenticated on purpose: the sign-in page has no session yet.

    Asserted explicitly because "add auth to it" is the obvious future edit and
    would break the only caller this endpoint exists for.
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/oauth/providers")
    assert resp.status_code == 200
    assert "detail" not in resp.json(), "the route started requiring credentials"


@pytest.mark.asyncio
async def test_providers_route_reports_real_configuration_state(monkeypatch):
    """`configured` must reflect this process, not a constant.

    A route that always answered `true` would be indistinguishable, from the
    sign-in page, from one that is correct -- right up until someone clicks a
    button for an IdP this deployment has no client secret for.
    """
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_ID", "configured-id")
    monkeypatch.setattr(settings, "GOOGLE_OAUTH_CLIENT_SECRET", "configured-secret")
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_ID", None)
    monkeypatch.setattr(settings, "GITHUB_OAUTH_CLIENT_SECRET", None)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/auth/oauth/providers")
    assert resp.status_code == 200
    by_name = {p["name"]: p["configured"] for p in resp.json()["providers"]}
    assert by_name["google"] is True
    assert by_name["github"] is False


@pytest.mark.asyncio
async def test_providers_route_is_not_swallowed_by_the_provider_catch_all():
    """The route-ordering bug this endpoint is most likely to reintroduce.

    FastAPI matches in registration order. If `/oauth/providers` is ever
    declared *after* `/oauth/{provider}`, the catch-all claims the request,
    `for_provider("providers")` returns a never-enabled stub, and the caller
    gets the "not configured" 404 -- which looks exactly like OAuth being
    unavailable, so the sign-in page would quietly render no buttons and no test
    anywhere would be red.
    """
    from backend.app.api.v1 import auth as auth_module

    paths = [
        route.path
        for route in auth_module.router.routes
        if getattr(route, "path", "").startswith("/auth/oauth")
    ]
    literal = paths.index("/auth/oauth/providers")
    catch_all = paths.index("/auth/oauth/{provider}")
    assert literal < catch_all, (
        f"/auth/oauth/providers is registered after /auth/oauth/{{provider}} and "
        f"is therefore unreachable; order is {paths}"
    )


def test_describe_providers_agrees_with_each_adapter_is_configured():
    """The registry-level helper the route delegates to.

    Checked against the adapters directly rather than against a snapshot,
    because a snapshot is a second list to keep in step -- which is the bug
    being fixed, one level down.
    """
    described = {d["name"]: d["configured"] for d in OAuthProviderRegistry.describe_providers()}
    for name in OAuthProviderRegistry.providers:
        adapter = OAuthProviderRegistry.get(name)
        assert described[name] == adapter.is_configured(), (
            f"describe_providers disagrees with {name}.is_configured()"
        )
    assert set(described) == set(OAuthProviderRegistry.providers)


# ─── _CacheOAuthStateStore: the Redis-backed default ──────────────────────
#
# Every other test here injects MemoryStateStore, which is exactly why this
# class shipped broken: `asyncio.wait_for` was called with no module-level
# `import asyncio`, the NameError was swallowed by a bare `except Exception:
# pass`, and state silently lived only in the class-level dict. Sign-in worked
# on one worker and failed on every other (and after any restart) with "Invalid
# or expired OAuth state". These tests drive the real class against a fake
# cache, so the import and the shared write are load-bearing.

class _FakeCache:
    """Minimal ICacheService stand-in recording set/get/delete."""

    def __init__(self, fail: bool = False) -> None:
        self.data: dict[str, str] = {}
        self.deleted: list[str] = []
        self.fail = fail

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> None:
        if self.fail:
            raise RuntimeError("cache down")
        self.data[key] = value

    async def get(self, key: str) -> str | None:
        if self.fail:
            raise RuntimeError("cache down")
        return self.data.get(key)

    async def delete(self, key: str) -> None:
        if self.fail:
            raise RuntimeError("cache down")
        self.deleted.append(key)
        self.data.pop(key, None)


def _fresh_cache_store(monkeypatch, fake: _FakeCache):
    from backend.app.services.oauth_service import _CacheOAuthStateStore

    monkeypatch.setattr(oauth_module, "get_cache_service", lambda: fake)
    # The dict is a class attribute, shared across instances; clear it so a
    # leftover state from another test cannot make a pop succeed.
    _CacheOAuthStateStore._mem_store.clear()
    return _CacheOAuthStateStore(ttl_seconds=600)


def test_cache_state_store_writes_state_through_to_the_shared_cache(monkeypatch):
    fake = _FakeCache()
    store = _fresh_cache_store(monkeypatch, fake)

    _await(store.set("st-shared", "verifier-shared"))

    assert fake.data == {"oauth:state:st-shared": "verifier-shared"}, (
        "state never reached the shared cache: it would be valid only in this "
        "worker and every other replica would reject it as unknown"
    )


def test_cache_state_store_pop_consumes_the_shared_copy_exactly_once(monkeypatch):
    fake = _FakeCache()
    store = _fresh_cache_store(monkeypatch, fake)
    _await(store.set("st-once", "verifier-once"))

    assert _await(store.pop("st-once")) == "verifier-once"

    # The shared entry is consumed and deleted, and the local copy is cleared,
    # so a replay from any process (or after a restart) must fail.
    assert "oauth:state:st-once" not in fake.data
    assert fake.deleted == ["oauth:state:st-once"]
    assert _await(store.pop("st-once")) is None


def test_cache_state_store_falls_back_to_local_memory_while_cache_is_down(monkeypatch):
    fake = _FakeCache(fail=True)
    store = _fresh_cache_store(monkeypatch, fake)

    # Neither call may raise: a cache outage must not block sign-in.
    _await(store.set("st-offline", "verifier-offline"))
    assert _await(store.pop("st-offline")) == "verifier-offline"
    assert _await(store.pop("st-offline")) is None


def test_cache_state_store_bounds_its_process_local_fallback(monkeypatch):
    """An abandoned sign-in is never popped, so the local dict must be capped.

    Every `set` adds a local entry; only a successful `pop` removes one. A user
    who starts a login and walks away leaves the entry behind for the life of
    the process, so without a cap the dict grows one `state` per abandoned
    login. The cache is forced down here so the local path is the only one and
    the shared store cannot mask what the local dict does.
    """
    from backend.app.services.oauth_service import _CacheOAuthStateStore

    fake = _FakeCache(fail=True)
    store = _fresh_cache_store(monkeypatch, fake)
    cap = _CacheOAuthStateStore._MEM_STORE_MAX

    for i in range(cap + 50):
        _await(store.set(f"st-{i}", f"verifier-{i}"))

    assert len(_CacheOAuthStateStore._mem_store) <= cap, (
        "the process-local state fallback grew past its cap; an abandoned "
        "sign-in leaks an entry for the life of the process"
    )
    # The newest survived and is still usable; the oldest was evicted.
    assert _await(store.pop(f"st-{cap + 49}")) == f"verifier-{cap + 49}"
    assert _await(store.pop("st-0")) is None
