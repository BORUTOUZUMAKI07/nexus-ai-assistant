"""
Tests for OAuth/OIDC SSO (Authorziation Code + PKCE).

Covers, all offline (the provider never hears from these tests):

  * OAuthService PKCE authorize-URL construction (RFC 7636 S256 challenge
    round-trips against the stored code_verifier) and enabled-gating.
  * complete_login: code -> token -> userinfo -> OAuthIdentity, with strict
    one-time-use state, and rejection of unknown state / missing / unverified
    email (account-takeover prevention).
  * AuthService.sso_login: find-or-provision by email, verified-lift, inactive
    rejection, TOTP preauth gating, collision-safe username allocation.
  * /auth/oauth/login + /auth/oauth/callback routes: 404 while disabled, and
    the full happy path wiring through the DI override seam.
"""
import asyncio
import base64
import hashlib
from urllib.parse import parse_qs, urlparse
from uuid import uuid4

import pytest
from backend.app.api import deps
from backend.app.core.exceptions import AuthenticationError
from backend.app.domain.user.models import User, UserSettings
from backend.app.domain.user.schemas import TokenResponse, TwoFactorChallengeResponse
from backend.app.main import app
from backend.app.services.auth_service import AuthService
from backend.app.services.oauth_service import OAuthService
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


def _make_service(
    *,
    store: MemoryStateStore | None = None,
    exchanger=None,
    fetcher=None,
    enabled: bool = True,
) -> OAuthService:
    common = dict(
        client_id="test-client" if enabled else None,
        client_secret="test-secret",
        authorize_url="https://idp.example/authorize" if enabled else None,
        token_url="https://idp.example/token",
        userinfo_url="https://idp.example/userinfo",
        scope="openid profile email",
        redirect_uri="http://localhost:8000/api/v1/auth/oauth/callback",
        state_store=store or MemoryStateStore(),
        code_exchanger=exchanger or _fake_exchange,
        userinfo_fetcher=fetcher or _fake_userinfo,
    )
    return OAuthService(**common)


def _await(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


def _s256_challenge(verifier: str) -> str:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


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


# ─── Routes ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_oauth_login_route_404_when_disabled():
    app.dependency_overrides[deps.get_oauth_service] = lambda: _make_service(enabled=False)
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/api/v1/auth/oauth/login")
        assert resp.status_code == 404
        assert "not configured" in resp.json()["detail"]
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
            resp = await client.get("/api/v1/auth/oauth/login")
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
                "/api/v1/auth/oauth/callback",
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
