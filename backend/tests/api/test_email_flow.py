"""
Unit tests for email-verification + password-reset token flows (fail-open):
signed tokens, wrong-purpose rejection, uniform forgot-password envelopes, and
dev-channel magic links — no SMTP/Resend required.
"""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pytest
from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import create_access_token, get_password_hash, verify_password
from backend.app.domain.user.models import User
from backend.app.services.auth import service as auth_module
from backend.app.services.auth.service import AuthService


def _await(coro):
    # pytest-asyncio tears the current loop down after each async test, so once
    # one has run get_event_loop() raises here. Re-establish a loop instead of
    # depending on collection order.
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    return loop.run_until_complete(coro)


class StubSession:
    def __init__(self):
        self.commits = 0

    def add(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


class FakeAuthRepo:
    def __init__(self, user):
        self.user = user
        self.updated = 0

    async def get_by_email(self, email):
        return self.user if self.user.email == email else None

    async def get_by_username(self, username):
        return self.user if self.user.username == username else None

    async def get_by_id(self, user_id):
        return self.user if self.user.id == user_id else None

    async def update(self, user, update_data):
        self.updated += 1
        for key, value in update_data.items():
            if value is not None and hasattr(user, key):
                setattr(user, key, value)
        return user


def _auth():
    user = User(
        email="flow@example.com",
        username="flow.user",
        hashed_password=get_password_hash("OldPass123!"),
        is_active=True,
        is_verified=False,
    )
    auth = AuthService(StubSession())
    auth._repo = FakeAuthRepo(user)
    # TwoFactorService is never consulted in these flows, but override its repo
    # with a plain stub so no DB call is possible.
    auth._two_factor._repo = FakeAuthRepo(user)
    return auth, user


@pytest.fixture
def capture_email(monkeypatch):
    sent = []

    async def fake_send(to, subject, body_html, body_text=None):
        sent.append({"to": to, "subject": subject, "html": body_html})
        return {"sent": False, "channel": "dev"}

    monkeypatch.setattr(auth_module.email_service, "send", fake_send)
    return sent


class MemoryCache:
    """In-memory stand-in for the Redis-backed ``ICacheService``.

    Password-reset single-use and refresh-family revocation both live behind
    ``get_cache_service``; a test that let those calls hit a real Redis would
    fail open (the service swallows a connection error) and silently pass while
    asserting nothing.
    """

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.store.get(key)

    async def set(self, key: str, value: str, ttl_seconds: int | None = None) -> bool:
        self.store[key] = value
        return True

    async def set_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        if key in self.store:
            return False
        self.store[key] = value
        return True

    async def delete(self, key: str) -> int:
        self.store.pop(key, None)
        return 1

    async def is_token_blacklisted(self, jti: str) -> bool:
        return f"blacklist:{jti}" in self.store

    async def blacklist_token(self, jti: str, ttl_seconds: int) -> None:
        self.store[f"blacklist:{jti}"] = "true"


@pytest.fixture
def cache(monkeypatch):
    fake = MemoryCache()
    monkeypatch.setattr(auth_module, "get_cache_service", lambda: fake)
    return fake


def _token_of(purpose, user_id):
    return create_access_token(
        subject=user_id,
        expires_delta=timedelta(minutes=30),
        token_type=purpose,
    )


def test_verify_email_marks_user_verified():
    auth, user = _auth()
    result = _await(auth.verify_email(_token_of("verify_email", user.id)))
    assert result.is_verified is True


def test_verify_email_rejects_wrong_purpose():
    auth, user = _auth()
    with pytest.raises(AuthenticationError):
        _await(auth.verify_email(_token_of("reset_password", user.id)))


def test_initiate_password_reset_sends_dev_link(capture_email, cache, monkeypatch):
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", True)
    auth, user = _auth()
    result = _await(auth.initiate_password_reset(user.email))
    assert result["channel"] == "email"
    assert "token=" in result["dev_link"]
    assert len(capture_email) == 1
    assert capture_email[0]["to"] == user.email


def test_initiate_password_reset_unknown_email_noop(capture_email, cache, monkeypatch):
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", False)
    auth, _ = _auth()
    result = _await(auth.initiate_password_reset("nobody@example.com"))
    # Uniform envelope: no account existence leak, no email fired.
    assert result == {"sent": True, "channel": "email", "dev_link": None}
    assert capture_email == []


async def test_forgot_password_envelope_is_identical_for_known_and_unknown(
    capture_email, cache, monkeypatch
):
    """The known-address branch must not be distinguishable by response body.

    The old code returned the mail service's result verbatim, so the known
    branch leaked ``channel: dev`` (or an SMTP error) while the unknown branch
    returned ``sent: False, channel: noop``. Any deviation here -- an extra
    key, a different ``sent`` -- is the enumeration oracle returning.
    """
    for dev_link in (False, True):
        monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", dev_link)
        auth, user = _auth()
        known = await auth.initiate_password_reset(user.email)
        unknown = await auth.initiate_password_reset("nobody@example.com")
        assert set(known) == set(unknown)
        assert known["sent"] is True and unknown["sent"] is True
        assert known["channel"] == unknown["channel"] == "email"
        # The key is present in both branches; only the value distinguishes
        # them, and that value is the credential the owner is entitled to.
        if dev_link:
            assert "token=" in known["dev_link"]
            assert unknown["dev_link"] is None
        else:
            assert known["dev_link"] is None
            assert unknown["dev_link"] is None
        # Drain any fire-and-forget send dispatched on the known branch so a
        # pending task cannot leak into the next test.
        await asyncio.sleep(0)


def test_reset_password_full_flow(capture_email, cache, monkeypatch):
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", True)
    auth, user = _auth()
    result = _await(auth.initiate_password_reset(user.email))
    token = result["dev_link"].split("token=", 1)[1]
    updated = _await(auth.reset_password(token, "BrandNewPass9!"))
    assert updated.is_verified is True
    assert verify_password("BrandNewPass9!", updated.hashed_password)


def test_reset_token_is_single_use(capture_email, cache, monkeypatch):
    """A reset token captured from a log/proxy cannot be replayed."""
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", True)
    auth, user = _auth()
    result = _await(auth.initiate_password_reset(user.email))
    token = result["dev_link"].split("token=", 1)[1]
    _await(auth.reset_password(token, "BrandNewPass9!"))
    with pytest.raises(AuthenticationError):
        _await(auth.reset_password(token, "AttackerPass9!"))
    # The second reset must not have taken effect.
    assert verify_password("BrandNewPass9!", user.hashed_password)


def test_reset_password_rejects_wrong_purpose():
    auth, user = _auth()
    with pytest.raises(AuthenticationError):
        _await(auth.reset_password(_token_of("verify_email", user.id), "SomethingElse9!"))


def test_send_verification_email_returns_dev_link(capture_email, cache, monkeypatch):
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", True)
    auth, user = _auth()
    result = _await(auth.send_verification_email(user))
    assert result["channel"] == "dev"
    assert "/verify-email?token=" in result["dev_link"]


def test_send_verification_email_hides_dev_link_by_default(capture_email, cache, monkeypatch):
    """Without the dev-link setting, no credential is returned to the caller."""
    monkeypatch.setattr(auth_module.settings, "EMAIL_DEV_LINK_ENABLED", False)
    auth, user = _auth()
    result = _await(auth.send_verification_email(user))
    assert result["dev_link"] is None
