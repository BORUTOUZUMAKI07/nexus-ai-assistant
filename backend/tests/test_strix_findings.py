"""Load-bearing regression tests for the 14 Strix findings.

Each test is written against the *fix*: reverting the fix must turn it red. The
harness ``backend/revert_strix.py`` applies each inverse to the real source, runs
the covering test, and requires a failure.

Phase 1 (auth) covers:

  * #2  logout did not revoke the refresh token (a 30-day session outlived sign-out)
  * #3  settings mass-assignment (a client could disable its own 2FA by PUT)
  * #4  X-Forwarded-For let a spoofed hop mint a fresh rate-limit key
  * #5/#8 forgot-password enumeration oracle + dev-link credential handoff
  * #9  TOTP codes were replayable within their window
  * #13 the username reached an HTML email body unescaped
"""
import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.core.client_ip import resolve_client_ip
from backend.app.core.exceptions import AuthenticationError
from backend.app.domain.user.models import User, UserSettings
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.schemas import UserCreate, UserSettingsUpdate
from backend.app.services import auth_service as auth_module
from backend.app.services.auth_service import AuthService
from backend.app.services.two_factor_service import TwoFactorService
from backend.tests.fakes import FakeSession
from pydantic import ValidationError


def _await(coro):
    # Sync test driving a coroutine directly. pytest-asyncio tears the current
    # loop down after each async test, so re-establish one instead of depending
    # on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class MemoryCache:
    """In-memory stand-in for the Redis-backed ``ICacheService``.

    Only the primitives the auth service uses are implemented, so the test stays
    honest about the interface surface it depends on.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}
        self.sets: list[tuple[str, int | None]] = []

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        self.sets.append((key, ttl_seconds))
        return True

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        if key in self.store:
            return False
        self.store[key] = value
        self.sets.append((key, ttl_seconds))
        return True

    async def delete(self, key: str) -> None:
        self.store.pop(key, None)


@pytest.fixture
def cache(monkeypatch):
    """Point AuthService at an in-memory cache for the duration of a test."""
    fake = MemoryCache()
    monkeypatch.setattr(auth_module, "get_cache_service", lambda: fake)
    return fake


def _seeded_service() -> tuple[FakeSession, AuthService, User]:
    fake = FakeSession()
    user = User(
        email=f"strix-{uuid4().hex[:8]}@nexus.ai",
        username=f"user{uuid4().hex[:6]}",
        role="user",
        is_active=True,
        is_verified=True,
    )
    fake.seed(User, [user])
    return fake, AuthService(fake), user


# ─── #2 logout must revoke the session server-side ───────────────────────────

def test_logout_with_only_access_token_revokes_the_refresh_family(cache):
    """A client that sends only an Authorization header still ends its session.

    The access token's ``fam`` claim is the only handle the server has when no
    refresh token is presented; without it the 30-day refresh token survives
    logout and a stolen copy keeps working.
    """
    _fake, svc, user = _seeded_service()
    pair = _await(svc._issue_token_pair(user))

    revoked = _await(svc.logout(access_token=pair.access_token, refresh_token=None))

    assert revoked is True
    # The session is genuinely dead: the refresh token can no longer be traded.
    with pytest.raises(AuthenticationError):
        _await(svc.refresh(pair.refresh_token))


def test_logout_with_refresh_token_revokes_the_session(cache):
    _fake, svc, user = _seeded_service()
    pair = _await(svc._issue_token_pair(user))

    assert _await(svc.logout(access_token=None, refresh_token=pair.refresh_token)) is True

    with pytest.raises(AuthenticationError):
        _await(svc.refresh(pair.refresh_token))


def test_logout_without_a_credential_reports_no_session(cache):
    """Nothing resolvable must not be reported as a successful logout."""
    _fake, svc, _user = _seeded_service()

    assert _await(svc.logout(access_token=None, refresh_token=None)) is False


def test_logout_route_returns_401_when_nothing_is_revoked(monkeypatch):
    """The route must not answer 200 for a logout it could not honour."""
    from backend.app.api import deps
    from backend.app.api.v1 import auth as auth_api
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    class _NoSessionService:
        async def logout(self, access_token, refresh_token):
            return False

    async def _allow(**_kwargs):
        return True, 99

    async def _run():
        monkeypatch.setattr(auth_api.redis_service, "check_rate_limit", _allow)
        app.dependency_overrides[deps.get_auth_service] = lambda: _NoSessionService()
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as client:
                return await client.post("/api/v1/auth/logout")
        finally:
            app.dependency_overrides.pop(deps.get_auth_service, None)

    response = _await(_run())

    assert response.status_code == 401
    assert "No active session" in response.json()["detail"]


# ─── #3 settings mass-assignment ─────────────────────────────────────────────

def test_settings_update_rejects_reserved_two_factor_keys():
    """A client write may not carry server-managed 2FA state."""
    for key in ("two_factor_enabled", "two_factor_last_counter"):
        with pytest.raises(ValidationError):
            UserSettingsUpdate(custom_settings={key: False})


def test_settings_update_allows_ordinary_custom_keys():
    """The guard is scoped to reserved keys, not to custom settings wholesale."""
    update = UserSettingsUpdate(custom_settings={"ui_density": "compact"})
    assert update.custom_settings == {"ui_density": "compact"}


def test_upsert_settings_merges_custom_settings_instead_of_replacing():
    """A merge must preserve the server-managed 2FA state in the same blob."""
    user_id = uuid4()
    fake = FakeSession()
    existing = UserSettings(
        user_id=user_id,
        custom_settings={"two_factor_enabled": True, "two_factor_last_counter": 41},
    )
    fake.seed(UserSettings, [existing])
    repo = UserRepository(fake)

    updated = _await(
        repo.upsert_settings(user_id, {"custom_settings": {"ui_density": "compact"}})
    )

    assert updated.custom_settings["ui_density"] == "compact"
    # The wholesale-replace bug erased both of these.
    assert updated.custom_settings["two_factor_enabled"] is True
    assert updated.custom_settings["two_factor_last_counter"] == 41


# ─── #4 spoofable rate-limit key ─────────────────────────────────────────────

class _Req:
    def __init__(self, peer: str, xff: str | None = None) -> None:
        self.client = SimpleNamespace(host=peer)
        self.headers = {} if xff is None else {"x-forwarded-for": xff}


def test_resolve_client_ip_ignores_xff_from_an_untrusted_peer():
    assert resolve_client_ip(_Req("203.0.113.9", "1.2.3.4"), []) == "203.0.113.9"


def test_resolve_client_ip_honours_xff_from_a_trusted_proxy():
    assert (
        resolve_client_ip(_Req("10.0.0.5", "198.51.100.7"), ["10.0.0.0/8"])
        == "198.51.100.7"
    )


def test_resolve_client_ip_trusts_loopback_without_an_allowlist():
    """The local dev proxy is loopback and writes the header itself."""
    assert resolve_client_ip(_Req("127.0.0.1", "198.51.100.7"), []) == "198.51.100.7"


def test_spoofed_forwarded_header_does_not_change_the_rate_limit_key(monkeypatch):
    """The core bypass: an untrusted peer prepends a fresh hop per request."""
    from backend.app.api.v1 import auth as auth_api

    seen: list[str] = []

    async def _record(**kwargs):
        seen.append(kwargs["identifier"])
        return True, 99

    monkeypatch.setattr(auth_api.redis_service, "check_rate_limit", _record)
    monkeypatch.setattr(auth_api.settings, "TRUSTED_PROXY_IPS", [])

    _await(
        auth_api._enforce_auth_rate_limit(
            _Req("203.0.113.9", "1.2.3.4"), action="login", limit=5
        )
    )

    assert seen == ["auth:login:203.0.113.9"]


def test_per_account_bucket_is_checked_so_ip_rotation_cannot_evade(monkeypatch):
    """Many source addresses must still hit one cap per target account."""
    from backend.app.api.v1 import auth as auth_api
    from fastapi import HTTPException

    checked: list[str] = []

    async def _deny_account(**kwargs):
        checked.append(kwargs["identifier"])
        return (not kwargs["identifier"].startswith("auth:login:acct:")), 0

    monkeypatch.setattr(auth_api.redis_service, "check_rate_limit", _deny_account)

    with pytest.raises(HTTPException) as excinfo:
        _await(
            auth_api._enforce_auth_rate_limit(
                _Req("203.0.113.9"),
                action="login",
                limit=5,
                account="victim@example.com",
            )
        )

    assert excinfo.value.status_code == 429
    assert any(i.startswith("auth:login:acct:") for i in checked)
    # The raw address must not be the key material.
    assert not any("victim@example.com" in i for i in checked)


# ─── #5/#8 email channel: a failure is not a dev sink, and never leaks ───────

def test_provider_failure_reports_failed_channel_not_dev(monkeypatch):
    """A configured-but-broken provider must not masquerade as the dev sink."""
    from backend.app.services import email_service as mod

    monkeypatch.setattr(mod.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(mod.settings, "SMTP_USER", "user")
    monkeypatch.setattr(mod.settings, "SMTP_PASSWORD", "pass")
    monkeypatch.setattr(mod.settings, "SMTP_TLS", False)
    monkeypatch.setattr(mod.settings, "SMTP_PORT", 587)

    class _BoomSMTP:
        def __init__(self, *args, **kwargs):
            raise OSError("smtp.internal.host connection refused")

    monkeypatch.setattr(mod, "SMTP", _BoomSMTP)

    result = _await(mod.email_service.send("a@b.com", "s", "<p>x</p>"))

    assert result["sent"] is False
    assert result["channel"] == "failed"
    # The provider error text names a host and must not reach the caller.
    assert "smtp.internal.host" not in result["message"]


def test_no_provider_still_uses_the_dev_channel(monkeypatch):
    from backend.app.services import email_service as mod

    monkeypatch.setattr(mod.settings, "RESEND_API_KEY", "")
    monkeypatch.setattr(mod.settings, "SMTP_USER", "")
    monkeypatch.setattr(mod.settings, "SMTP_PASSWORD", "")

    result = _await(mod.email_service.send("a@b.com", "s", "<p>x</p>"))

    assert result["channel"] == "dev"


def test_resend_rejection_reports_failed_without_leaking_the_provider(monkeypatch):
    from backend.app.services import email_service as mod

    monkeypatch.setattr(mod.settings, "RESEND_API_KEY", "test-key")

    class _Resp:
        status_code = 500

    class _FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def post(self, *args, **kwargs):
            return _Resp()

    monkeypatch.setattr(mod.httpx, "AsyncClient", lambda *a, **k: _FakeClient())

    result = _await(mod.email_service.send("a@b.com", "s", "<p>x</p>"))

    assert result["channel"] == "failed"
    assert "500" not in result["message"]
    assert "api.resend.com" not in result["message"]


# ─── #9 TOTP replay ──────────────────────────────────────────────────────────

def _seeded_two_factor(secret: str, enabled: bool = True):
    user_id = uuid4()
    fake = FakeSession()
    row = UserSettings(
        user_id=user_id,
        totp_secret=secret,
        custom_settings={"two_factor_enabled": enabled},
    )
    fake.seed(UserSettings, [row])
    return user_id, TwoFactorService(fake), row


def test_totp_code_is_single_use():
    import pyotp

    secret = pyotp.random_base32()
    user_id, svc, _row = _seeded_two_factor(secret)
    code = pyotp.TOTP(secret).now()

    assert _await(svc.verify_login_code(user_id, code)) is True
    # The same code, still inside its window, must not authenticate again.
    assert _await(svc.verify_login_code(user_id, code)) is False


def test_enable_consumes_the_code_so_verify_cannot_reuse_it():
    import pyotp

    secret = pyotp.random_base32()
    user_id, svc, _row = _seeded_two_factor(secret, enabled=False)
    code = pyotp.TOTP(secret).now()

    assert _await(svc.enable(user_id, code)) is True
    # Turning 2FA on with a code must not leave that code usable at login.
    assert _await(svc.verify_login_code(user_id, code)) is False


# ─── #13 stored HTML injection through the username ──────────────────────────

def test_username_allowlist_rejects_markup():
    with pytest.raises(ValidationError):
        UserCreate(
            email="a@b.com",
            username="<script>alert(1)</script>",
            password="correct-horse-battery",
        )


def test_username_allowlist_accepts_ordinary_handles():
    created = UserCreate(
        email="a@b.com", username="Ada Lovelace_01", password="correct-horse-battery"
    )
    assert created.username == "Ada Lovelace_01"


def test_escape_html_neutralises_markup():
    from backend.app.services.email_service import escape_html

    assert escape_html("<img src=x onerror=alert(1)>") == (
        "&lt;img src=x onerror=alert(1)&gt;"
    )
    assert escape_html(None) == ""
