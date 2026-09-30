"""
Tests for provider-based OAuth/SSO (Authorization Code + PKCE).

Covers, all offline (the provider never hears from these tests):

  * OAuthService PKCE authorize-URL construction (RFC 7636 S256 challenge
    round-trips against the stored code_verifier) and enabled-gating.
  * complete_login: code -> token -> userinfo -> OAuthIdentity, with strict
    one-time-use state, and rejection of unknown state / missing / unverified
    email (account-takeover prevention).
  * The provider registry: google + github registered, unknown names disabled.
  * Google/GitHub provider authorize-URL builds (scopes, redirect URIs, PKCE
    params) and GitHub's verified-primary-email selection.
  * AuthService.sso_login: find-or-provision by email, verified-lift, inactive
    rejection, TOTP preauth gating, collision-safe username allocation.
  * /auth/oauth/{provider} + /auth/oauth/{provider}/callback routes: 404 while
    disabled, and the full happy path wiring through the DI override seam.
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
    OAuthService,
    SSOProviderRegistry,
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
    return {"access_token": "sso-token", "token_type": "Bearer"}


async def _fake_userinfo(token: dict) -> dict:
    return {
        "sub": "idp-subject-123",
        "email": "sso@nexus.ai",
        "email_verified": True,
        "name": "SSO User",
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
    assert set(SSOProviderRegistry.list_providers()) == {"google", "github"}
    assert SSOProviderRegistry.get("google") is not None
    assert SSOProviderRegistry.get("github") is not None


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
    assert identity.email == "sso@nexus.ai"
    assert identity.email_verified is True
    assert identity.name == "SSO User"
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


# ─── AuthService.sso_login ────────────────────────────────────────────────

def _identity(**overrides):
    from backend.app.services.oauth_service import OAuthIdentity

    base = dict(
        provider="oidc",
        subject="idp-subject-123",
        email="sso@nexus.ai",
        email_verified=True,
        name="SSO User",
        picture="https://cdn.example/avatar.png",
    )
    base.update(overrides)
    return OAuthIdentity(**base)


def test_sso_provisions_new_user_and_issues_tokens():
    fake = FakeSession()
    svc = AuthService(fake)

    result = _await(svc.sso_login(_identity()))

    assert isinstance(result, TokenResponse)
    assert result.access_token and result.refresh_token
    rows = fake.rows.get(User, [])
    assert len(rows) == 1
    user = rows[0]
    assert user.email == "sso@nexus.ai"
    assert user.username == "sso"
    assert user.hashed_password is None  # passwordless SSO account
    assert user.is_verified is True
    assert user.full_name == "SSO User"
    assert fake.rows.get(UserSettings, [])  # default settings row created


def test_sso_existing_user_is_verified_and_gets_tokens():
    fake = FakeSession()
    user = User(email="sso@nexus.ai", username="sso", is_verified=False)
    fake.seed(User, [user])
    svc = AuthService(fake)

    result = _await(svc.sso_login(_identity()))

    assert isinstance(result, TokenResponse)
    assert user.is_verified is True
    assert len(fake.rows.get(User, [])) == 1  # no duplicate provisioning


def test_sso_inactive_account_rejected():
    fake = FakeSession()
    fake.seed(User, [User(email="sso@nexus.ai", username="sso", is_active=False)])
    svc = AuthService(fake)

    with pytest.raises(AuthenticationError, match="inactive"):
        _await(svc.sso_login(_identity()))


def test_sso_totp_account_gets_preauth_challenge():
    fake = FakeSession()
    user = User(email="sso@nexus.ai", username="sso", is_verified=True)
    fake.seed(User, [user])
    fake.seed(
        UserSettings,
        [UserSettings(user_id=user.id, totp_secret="FAKESECRET",
                      custom_settings={"two_factor_enabled": True})],
    )
    svc = AuthService(fake)

    result = _await(svc.sso_login(_identity()))

    assert isinstance(result, TwoFactorChallengeResponse)
    assert result.status == "2fa_required"
    assert result.preauth_token


def test_sso_username_collision_gets_suffix():
    fake = FakeSession()
    fake.seed(User, [User(email="other@nexus.ai", username="sso")])
    svc = AuthService(fake)

    result = _await(svc.sso_login(_identity()))

    assert isinstance(result, TokenResponse)
    provisioned = [u for u in fake.rows.get(User, []) if u.email == "sso@nexus.ai"]
    assert len(provisioned) == 1
    assert provisioned[0].username != "sso"
    assert provisioned[0].username.startswith("sso-")


def test_sso_requires_subject():
    fake = FakeSession()
    svc = AuthService(fake)
    with pytest.raises(AuthenticationError, match="identity"):
        _await(svc.sso_login(_identity(subject="")))


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


def test_sso_login_rejects_unverified_identity():
    """Defence in depth: sso_login itself refuses an unverified identity.

    ``OAuthService`` already gates this, so the only way to get here is a caller
    that skipped that gate — the check must not be reachable-around.
    """
    fake = FakeSession()
    svc = AuthService(fake)
    with pytest.raises(AuthenticationError, match="not verified"):
        _await(svc.sso_login(_identity(email_verified=False)))
    assert fake.rows.get(User, []) == []  # nothing provisioned


def test_sso_binds_existing_account_to_provider_subject():
    """First SSO login for a password account records the provider linkage."""
    fake = FakeSession()
    user = User(email="sso@nexus.ai", username="sso", is_verified=True)
    fake.seed(User, [user])
    svc = AuthService(fake)

    _await(svc.sso_login(_identity()))

    assert user.oauth_provider == "oidc"
    assert user.oauth_sub == "idp-subject-123"
    assert len(fake.rows.get(User, [])) == 1  # adopted, not duplicated


def test_sso_resolves_by_subject_when_provider_reassigns_email():
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

    result = _await(svc.sso_login(_identity(email="reassigned@nexus.ai")))

    assert isinstance(result, TokenResponse)
    # Exactly one account, and the linked one — no duplicate for the new email.
    assert len(fake.rows.get(User, [])) == 1
    assert linked.email == "old-address@nexus.ai"  # we never rewrite it


def test_sso_new_account_stores_provider_identity():
    """A provisioned SSO account records the subject that created it."""
    fake = FakeSession()
    svc = AuthService(fake)

    _await(svc.sso_login(_identity()))

    user = fake.rows.get(User, [])[0]
    assert user.oauth_provider == "oidc"
    assert user.oauth_sub == "idp-subject-123"


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
        assert any(u.email == "sso@nexus.ai" for u in fake.rows.get(User, []))
    finally:
        app.dependency_overrides.pop(deps.get_oauth_service, None)
        app.dependency_overrides.pop(deps.get_auth_service, None)
