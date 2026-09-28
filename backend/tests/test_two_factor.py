"""
Unit tests for TOTP two-factor authentication: setup/enable/disable flows,
login challenge + preauth exchange (RFC 6238), using in-memory fakes.
"""
import asyncio
from datetime import timedelta
from uuid import uuid4

import pyotp
import pytest
from backend.app.core.exceptions import AuthenticationError
from backend.app.core.security import create_access_token, get_password_hash
from backend.app.domain.user.models import User, UserSettings
from backend.app.domain.user.schemas import (
    TokenResponse,
    TwoFactorChallengeResponse,
)
from backend.app.services.two_factor_service import TwoFactorService
from backend.app.services.auth_service import AuthService


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
    """Minimal session stand-in: services only add/commit/refresh existing rows."""

    def __init__(self):
        self.commits = 0

    def add(self, obj):
        pass

    async def commit(self):
        self.commits += 1

    async def refresh(self, obj):
        pass


class FakeSettingsRepo:
    def __init__(self, row):
        self.row = row

    async def get_settings(self, user_id):
        return self.row


class FakeAuthRepo:
    """UserRepository stand-in for the AuthService login/2FA interplay."""

    def __init__(self, user, settings_row):
        self.user = user
        self.settings_row = settings_row

    async def get_by_email(self, email):
        return self.user if self.user.email == email else None

    async def get_by_username(self, username):
        return self.user if self.user.username == username else None

    async def get_by_id(self, user_id):
        return self.user if self.user.id == user_id else None

    async def update(self, user, update_data):
        for key, value in update_data.items():
            if value is not None and hasattr(user, key):
                setattr(user, key, value)
        return user


def _make_service(user_email: str, two_factor: bool = False):
    secret = pyotp.random_base32() if two_factor else None
    settings_row = UserSettings(
        user_id=uuid4(),
        totp_secret=secret,
        custom_settings={"two_factor_enabled": two_factor} if two_factor else {},
    )
    user = User(
        email=user_email,
        username="tfa.user",
        hashed_password=get_password_hash("S3cretPass!"),
        is_active=True,
    )
    svc = TwoFactorService(StubSession())
    svc._repo = FakeSettingsRepo(settings_row)
    return svc, settings_row, user


def test_setup_returns_secret_and_uri():
    svc, row, _ = _make_service("a@example.com", two_factor=False)
    result = _await(svc.setup(row.user_id, "a@example.com"))
    assert len(result["secret"]) >= 16
    assert "otpauth://totp/" in result["otpauth_uri"]
    assert row.totp_secret == result["secret"]


def test_setup_twice_is_rejected():
    svc, row, _ = _make_service("a@example.com", two_factor=False)
    _await(svc.setup(row.user_id, "a@example.com"))
    with pytest.raises(ValueError):
        _await(svc.setup(row.user_id, "a@example.com"))


def test_setup_enable_roundtrip():
    svc, row, _ = _make_service("a@example.com")
    setup = _await(svc.setup(row.user_id, "a@example.com"))
    code = pyotp.TOTP(setup["secret"]).now()
    assert _await(svc.enable(row.user_id, code)) is True
    assert (row.custom_settings or {}).get("two_factor_enabled") is True
    assert _await(svc.is_enabled(row.user_id)) is True


def test_enable_rejects_wrong_code():
    svc, row, _ = _make_service("a@example.com")
    _await(svc.setup(row.user_id, "a@example.com"))
    assert _await(svc.enable(row.user_id, "000000")) is False
    assert _await(svc.is_enabled(row.user_id)) is False


def test_disable_clears_secret_and_flag():
    svc, row, _ = _make_service("a@example.com", two_factor=True)
    assert _await(svc.is_enabled(row.user_id))
    code = pyotp.TOTP(row.totp_secret).now()
    assert _await(svc.disable(row.user_id, code)) is True
    assert row.totp_secret is None
    assert _await(svc.is_enabled(row.user_id)) is False


def test_verify_login_code():
    svc, row, _ = _make_service("a@example.com", two_factor=True)
    code = pyotp.TOTP(row.totp_secret).now()
    assert _await(svc.verify_login_code(row.user_id, code)) is True
    assert _await(svc.verify_login_code(row.user_id, "123456")) is False


def _auth_with_2fa():
    secret = pyotp.random_base32()
    settings_row = UserSettings(
        user_id=uuid4(),
        totp_secret=secret,
        custom_settings={"two_factor_enabled": True},
    )
    user = User(
        email="b@example.com",
        username="tfa.b",
        hashed_password=get_password_hash("S3cretPass!"),
        is_active=True,
    )
    auth = AuthService(StubSession())
    auth._repo = FakeAuthRepo(user, settings_row)
    auth._two_factor._repo = FakeSettingsRepo(settings_row)
    return auth, secret


def test_login_returns_challenge_when_2fa_enabled():
    """AuthService.login yields a preauth challenge instead of tokens when 2FA is on."""
    auth, secret = _auth_with_2fa()
    result = _await(auth.login("b@example.com", "S3cretPass!"))
    assert isinstance(result, TwoFactorChallengeResponse)
    assert result.status == "2fa_required"
    assert result.preauth_token
    assert result.expires_in > 0


def test_verify_2fa_exchanges_challenge_for_tokens():
    auth, secret = _auth_with_2fa()
    challenge = _await(auth.login("b@example.com", "S3cretPass!"))
    code = pyotp.TOTP(secret).now()
    tokens = _await(auth.verify_2fa(challenge.preauth_token, code))
    assert isinstance(tokens, TokenResponse)
    assert tokens.access_token and tokens.refresh_token

    with pytest.raises(AuthenticationError):
        _await(auth.verify_2fa(challenge.preauth_token, "000000"))


def test_login_returns_tokens_when_2fa_disabled():
    settings_row = UserSettings(user_id=uuid4(), totp_secret=None, custom_settings={})
    user = User(
        email="c@example.com",
        username="tfa.c",
        hashed_password=get_password_hash("S3cretPass!"),
        is_active=True,
    )
    auth = AuthService(StubSession())
    auth._repo = FakeAuthRepo(user, settings_row)
    auth._two_factor._repo = FakeSettingsRepo(settings_row)

    result = _await(auth.login("c@example.com", "S3cretPass!"))
    assert isinstance(result, TokenResponse)

    with pytest.raises(AuthenticationError):
        _await(auth.login("c@example.com", "wrong-password"))


@pytest.mark.asyncio
async def test_verify_2fa_rejects_access_token():
    """An ordinary access token must never be accepted as a preauth challenge."""
    auth, secret = _auth_with_2fa()
    access = create_access_token(
        subject=auth._repo.user.id,
        expires_delta=timedelta(minutes=5),
        additional_claims={"role": auth._repo.user.role},
    )
    with pytest.raises(AuthenticationError):
        await auth.verify_2fa(access, pyotp.TOTP(secret).now())