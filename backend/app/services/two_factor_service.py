"""
TOTP two-factor authentication service (RFC 6238).

Uses the existing ``users.user_settings`` row for state — no schema migration:
  * ``totp_secret``              → the TOTP secret (set at setup, cleared at disable)
  * ``custom_settings["two_factor_enabled"]`` → the enabled flag

Login flow: when a user has 2FA enabled, ``POST /auth/login`` returns a short-lived
``preauth`` JWT instead of a token pair; ``POST /auth/2fa/verify`` exchanges that
preauth token + a valid TOTP code for the real token pair.
"""
from datetime import UTC, datetime
from uuid import UUID

import structlog
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.models import UserSettings
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:  # pragma: no cover - pyotp is a hard dependency
    pyotp = None
    PYOTP_AVAILABLE = False


class TwoFactorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = UserRepository(session)

    async def _settings(self, user_id: UUID) -> UserSettings | None:
        return await self._repo.get_settings(user_id)

    @staticmethod
    def _verify_code(secret: str, code: str) -> bool:
        if not PYOTP_AVAILABLE or not secret:
            return False
        totp = pyotp.TOTP(secret)
        return totp.verify(code, valid_window=1)

    async def is_enabled(self, user_id: UUID) -> bool:
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            return False
        return bool((settings_row.custom_settings or {}).get("two_factor_enabled"))

    async def setup(self, user_id: UUID, email: str) -> dict:
        """Generate + persist a TOTP secret (pending enable). Returns display data once."""
        settings_row = await self._settings(user_id)
        if not settings_row:
            settings_row = UserSettings(user_id=user_id)
            self.session.add(settings_row)
            await self.session.commit()
            await self.session.refresh(settings_row)
        if settings_row.totp_secret:
            raise ValueError("Two-factor authentication is already provisioned for this account.")
        if not PYOTP_AVAILABLE:
            raise RuntimeError("pyotp is not installed; cannot provision TOTP.")

        secret = pyotp.random_base32()
        settings_row.totp_secret = secret
        self.session.add(settings_row)
        await self.session.commit()
        await self.session.refresh(settings_row)
        uri = pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name="Nexus AI Assistant")
        logger.info("two_factor_setup_pending", user_id=str(user_id))
        return {"secret": secret, "otpauth_uri": uri}

    async def enable(self, user_id: UUID, code: str) -> bool:
        """Verify a code against the pending secret; enable 2FA on success."""
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            raise ValueError("No pending two-factor setup. Call POST /auth/2fa/setup first.")
        if not self._verify_code(settings_row.totp_secret, code):
            return False
        custom = dict(settings_row.custom_settings or {})
        custom["two_factor_enabled"] = True
        settings_row.custom_settings = custom
        settings_row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(settings_row)
        await self.session.commit()
        logger.info("two_factor_enabled", user_id=str(user_id))
        return True

    async def disable(self, user_id: UUID, code: str) -> bool:
        """Disable 2FA after validating the current code."""
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            raise ValueError("Two-factor authentication is not enabled on this account.")
        if not self._verify_code(settings_row.totp_secret, code):
            return False
        settings_row.totp_secret = None
        custom = dict(settings_row.custom_settings or {})
        custom["two_factor_enabled"] = False
        settings_row.custom_settings = custom
        settings_row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.add(settings_row)
        await self.session.commit()
        logger.info("two_factor_disabled", user_id=str(user_id))
        return True

    async def verify_login_code(self, user_id: UUID, code: str) -> bool:
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            return False
        return self._verify_code(settings_row.totp_secret, code)