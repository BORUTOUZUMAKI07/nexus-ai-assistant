"""
TOTP two-factor authentication service (RFC 6238).

Uses the existing ``users.user_settings`` row for state — no schema migration:
  * ``totp_secret``              → the TOTP secret (set at setup, cleared at disable),
    AES-256-GCM encrypted at rest (same envelope as BYOK keys)
  * ``custom_settings["two_factor_enabled"]`` → the enabled flag
  * ``custom_settings["two_factor_last_counter"]`` → the last TOTP step redeemed,
    which makes every code single-use (replay of an observed code is refused)
  * ``custom_settings["two_factor_recovery_codes"]`` → list of argon2-hashed
    one-time recovery codes (generated at enable, consumed on use)

Login flow: when a user has 2FA enabled, ``POST /auth/login`` returns a short-lived
``preauth`` JWT instead of a token pair; ``POST /auth/2fa/verify`` exchanges that
preauth token + a valid TOTP code for the real token pair.
"""
import hmac
import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.core.security import decrypt_api_key, encrypt_api_key, get_password_hash
from backend.app.domain.user.models import UserSettings
from backend.app.domain.user.repository import UserRepository
from passlib.context import CryptContext
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)

pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")

try:
    import pyotp
    PYOTP_AVAILABLE = True
except ImportError:  # pragma: no cover - pyotp is a hard dependency
    pyotp = None
    PYOTP_AVAILABLE = False

# 8 one-time recovery codes are generated at enable; each can be used once.
_RECOVERY_CODE_COUNT = 8
_RECOVERY_CODE_LEN = 10


class TwoFactorService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self._repo = UserRepository(session)

    async def _settings(self, user_id: UUID) -> UserSettings | None:
        return await self._repo.get_settings(user_id)

    # ── TOTP secret encryption (at rest, same envelope as BYOK) ──
    @staticmethod
    def _encrypt_secret(plain_secret: str) -> str:
        return encrypt_api_key(plain_secret)

    @staticmethod
    def _decrypt_secret(encrypted_secret: str) -> str:
        return decrypt_api_key(encrypted_secret)

    # ── Recovery codes ──
    @staticmethod
    def _generate_recovery_codes() -> tuple[list[str], list[str]]:
        """Return (plaintext_codes, argon2_hashes). Plaintext shown once to the user."""
        plaintext: list[str] = []
        hashes: list[str] = []
        for _ in range(_RECOVERY_CODE_COUNT):
            code = secrets.token_urlsafe(_RECOVERY_CODE_LEN)[:_RECOVERY_CODE_LEN]
            plaintext.append(code)
            hashes.append(get_password_hash(code))
        return plaintext, hashes

    @staticmethod
    def _verify_recovery_code(code: str, stored_hash: str) -> bool:
        return pwd_context.verify(code, stored_hash)

    @staticmethod
    def _matching_counter(secret: str, code: str) -> int | None:
        """Return the TOTP time-step that ``code`` matches, or None.

        Returning the counter (not just a bool) is what makes single-use
        possible: the caller records the accepted step and refuses any code for
        a step it has already accepted, so a code observed on the wire cannot be
        replayed inside its ~90s validity window.
        """
        if not PYOTP_AVAILABLE or not secret:
            return None
        totp = pyotp.TOTP(secret)
        now = datetime.now(UTC)
        interval = totp.interval
        for offset in range(-settings.TOTP_VALID_WINDOW, settings.TOTP_VALID_WINDOW + 1):
            candidate = now + timedelta(seconds=offset * interval)
            if hmac.compare_digest(str(totp.at(candidate)), str(code)):
                return totp.timecode(candidate)
        return None

    @staticmethod
    def _accept_counter(settings_row: UserSettings, counter: int) -> bool:
        """Record ``counter`` as redeemed; False when it was already consumed.

        Shared by enable/disable/verify so a code used to turn 2FA on (or off)
        cannot be redeemed a second time at the login step, and so a strictly
        increasing comparison stops any earlier step from being replayed after
        a later one was accepted.
        """
        custom = dict(settings_row.custom_settings or {})
        last = custom.get("two_factor_last_counter")
        if isinstance(last, int) and counter <= last:
            return False
        custom["two_factor_last_counter"] = counter
        settings_row.custom_settings = custom
        settings_row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        return True

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
        settings_row.totp_secret = self._encrypt_secret(secret)
        self.session.add(settings_row)
        await self.session.commit()
        await self.session.refresh(settings_row)
        uri = pyotp.TOTP(secret).provisioning_uri(
            name=email, issuer_name=settings.TOTP_ISSUER
        )
        logger.info("two_factor_setup_pending", user_id=str(user_id))
        return {"secret": secret, "otpauth_uri": uri}

    async def enable(self, user_id: UUID, code: str) -> bool:
        """Verify a code against the pending secret; enable 2FA on success.

        Recovery codes are generated and stored (hashed) as a side effect;
        the caller can return them to the user via a separate response.
        """
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            raise ValueError("No pending two-factor setup. Call POST /auth/2fa/setup first.")
        secret = self._decrypt_secret(settings_row.totp_secret)
        counter = self._matching_counter(secret, code)
        if counter is None or not self._accept_counter(settings_row, counter):
            return False
        custom = dict(settings_row.custom_settings or {})
        custom["two_factor_enabled"] = True
        _, recovery_hashes = self._generate_recovery_codes()
        custom["two_factor_recovery_codes"] = recovery_hashes
        settings_row.custom_settings = custom
        self.session.add(settings_row)
        await self.session.commit()
        logger.info("two_factor_enabled", user_id=str(user_id))
        return True

    async def disable(self, user_id: UUID, code: str) -> bool:
        """Disable 2FA after validating the current code."""
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            raise ValueError("Two-factor authentication is not enabled on this account.")
        secret = self._decrypt_secret(settings_row.totp_secret)
        counter = self._matching_counter(secret, code)
        if counter is None or not self._accept_counter(settings_row, counter):
            return False
        settings_row.totp_secret = None
        custom = dict(settings_row.custom_settings or {})
        custom["two_factor_enabled"] = False
        custom.pop("two_factor_recovery_codes", None)
        settings_row.custom_settings = custom
        self.session.add(settings_row)
        await self.session.commit()
        logger.info("two_factor_disabled", user_id=str(user_id))
        return True

    async def verify_login_code(self, user_id: UUID, code: str) -> bool:
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            return False
        secret = self._decrypt_secret(settings_row.totp_secret)
        counter = self._matching_counter(secret, code)
        if counter is None:
            return False
        if not self._accept_counter(settings_row, counter):
            # The code is valid for its window but this window was already
            # redeemed: a replay of an observed code, not a fresh login.
            logger.warning("two_factor_code_replayed", user_id=str(user_id))
            return False
        self.session.add(settings_row)
        await self.session.commit()
        return True

    async def verify_recovery_code(self, user_id: UUID, code: str) -> bool:
        """Consume a one-time recovery code. Returns True if valid and unused."""
        settings_row = await self._settings(user_id)
        if not settings_row or not settings_row.totp_secret:
            return False
        custom = dict(settings_row.custom_settings or {})
        hashes = custom.get("two_factor_recovery_codes") or []
        for i, stored_hash in enumerate(hashes):
            if self._verify_recovery_code(code, stored_hash):
                # Consume this code by removing it from the list.
                hashes.pop(i)
                custom["two_factor_recovery_codes"] = hashes
                settings_row.custom_settings = custom
                self.session.add(settings_row)
                await self.session.commit()
                logger.info("two_factor_recovery_code_consumed", user_id=str(user_id))
                return True
        return False
