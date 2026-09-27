"""
Auth Application Service.
Owns all authentication and user registration use cases (SRP).
Route handlers depend on this abstraction, not on UserRepository directly (DIP).
"""
import secrets
import time
from datetime import timedelta
from uuid import UUID

import structlog
from backend.app.core.config import settings
from backend.app.core.exceptions import (
    AuthenticationError,
    UserAlreadyExistsError,
)
from backend.app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    get_password_hash,
    verify_password,
)
from backend.app.domain.user.models import User, UserSettings
from backend.app.domain.user.repository import UserRepository
from backend.app.domain.user.schemas import (
    TokenResponse,
    TwoFactorChallengeResponse,
    UserCreate,
)
from backend.app.infrastructure.cache.redis_client import get_cache_service
from backend.app.services.email_service import build_email_link, email_service
from backend.app.services.oauth_service import OAuthIdentity
from backend.app.services.two_factor_service import TwoFactorService
from sqlmodel.ext.asyncio.session import AsyncSession

logger = structlog.get_logger(__name__)


class AuthService:
    """
    Application service for authentication use cases.
    Injected with AsyncSession; does not expose repository internals to routes.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repo = UserRepository(session)
        self._two_factor = TwoFactorService(session)

    async def register(self, user_in: UserCreate) -> User:
        """Register a new user, raising on duplicate email or username."""
        if await self._repo.get_by_email(user_in.email):
            raise UserAlreadyExistsError(
                f"A user with email '{user_in.email}' already exists."
            )
        if await self._repo.get_by_username(user_in.username):
            raise UserAlreadyExistsError(
                f"A user with username '{user_in.username}' already exists."
            )
        user = await self._repo.create(user_in)
        logger.info("user_registered", user_id=str(user.id))
        return user

    async def login(self, identifier: str, password: str) -> TokenResponse | TwoFactorChallengeResponse:
        """Authenticate user by email or username + password, return token pair.

        When the user has TOTP 2FA enabled, a short-lived ``preauth`` challenge
        token is returned instead; the client exchanges it for the real token
        pair via POST /auth/2fa/verify.
        """
        # OAuth2PasswordRequestForm accepts unbounded form strings, so enforce
        # credential limits here as well as in the JSON registration schemas.
        if (
            not isinstance(identifier, str)
            or not identifier.strip()
            or len(identifier) > 320
            or not isinstance(password, str)
            or not 1 <= len(password) <= 128
        ):
            raise AuthenticationError("Incorrect email/username or password.")
        user = await self._repo.get_by_email(identifier)
        if not user:
            user = await self._repo.get_by_username(identifier)

        if (
            not user
            or user.hashed_password is None
            or not verify_password(password, user.hashed_password)
        ):
            raise AuthenticationError("Incorrect email/username or password.")
        if not user.is_active:
            raise AuthenticationError("User account is inactive.")
        if settings.EMAIL_VERIFICATION_REQUIRED and not user.is_verified:
            raise AuthenticationError("Please verify your email address before logging in.")

        if await self._two_factor.is_enabled(user.id):
            preauth = create_access_token(
                subject=user.id,
                expires_delta=timedelta(minutes=settings.TOTP_PREAUTH_MINUTES),
                token_type="preauth",
            )
            logger.info("two_factor_challenge_issued", user_id=str(user.id))
            return TwoFactorChallengeResponse(
                status="2fa_required",
                preauth_token=preauth,
                expires_in=settings.TOTP_PREAUTH_MINUTES * 60,
            )

        return await self._issue_token_pair(user)

    async def verify_2fa(self, preauth_token: str, code: str) -> TokenResponse:
        """Exchange a preauth challenge + valid TOTP code for a token pair."""
        payload = decode_token(preauth_token)
        if payload.get("type") != "preauth":
            raise AuthenticationError("Invalid or expired two-factor challenge.")
        sub_str = payload.get("sub")
        try:
            user_id = UUID(str(sub_str))
        except (ValueError, TypeError):
            raise AuthenticationError("Invalid challenge subject.")
        user = await self._repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive.")
        ok = await self._two_factor.verify_login_code(user_id, code)
        if not ok:
            raise AuthenticationError("Invalid two-factor authentication code.")
        logger.info("two_factor_verified", user_id=str(user_id))
        return await self._issue_token_pair(user)

    async def _issue_token_pair(self, user: User) -> TokenResponse:
        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            additional_claims={"role": user.role, "email": user.email},
        )
        refresh_token = create_refresh_token(subject=user.id)
        logger.info("user_logged_in", user_id=str(user.id))
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",  # nosec B106
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def sso_login(self, identity: OAuthIdentity) -> TokenResponse | TwoFactorChallengeResponse:
        """Sign a user in via a verified OIDC identity (find-or-provision by email).

        The IdP verifying the email is a precondition of ``OAuthService``, so a
        provisioned account is born verified. Existing accounts get their
        ``is_verified`` flag lifted. TOTP-enabled accounts receive the same
        ``2fa_required`` preauth challenge as password login.
        """
        email = (identity.email or "").lower().strip()
        if not email or not identity.subject:
            raise AuthenticationError("OAuth provider returned an unusable identity.")

        user = await self._repo.get_by_email(email)
        if user is None:
            user = await self._provision_oauth_user(identity, email)
        elif not user.is_active:
            raise AuthenticationError("User account is inactive.")

        if not user.is_verified:
            await self._repo.update(user, {"is_verified": True})

        if await self._two_factor.is_enabled(user.id):
            preauth = create_access_token(
                subject=user.id,
                expires_delta=timedelta(minutes=settings.TOTP_PREAUTH_MINUTES),
                token_type="preauth",
            )
            logger.info("two_factor_challenge_issued", user_id=str(user.id), via="sso")
            return TwoFactorChallengeResponse(
                status="2fa_required",
                preauth_token=preauth,
                expires_in=settings.TOTP_PREAUTH_MINUTES * 60,
            )

        return await self._issue_token_pair(user)

    async def _provision_oauth_user(self, identity: OAuthIdentity, email: str) -> User:
        """Auto-provision a passwordless SSO account (the IdP vouches for it)."""
        user = User(
            email=email,
            username=await self._alloc_username(email),
            hashed_password=None,  # SSO accounts have no password
            full_name=identity.name,
            avatar_url=identity.picture,
            is_verified=True,
        )
        self._session.add(user)
        await self._session.commit()
        await self._session.refresh(user)
        # Mirror the default user-settings row that password registration
        # creates, so SSO accounts behave identically downstream.
        self._session.add(UserSettings(user_id=user.id))
        await self._session.commit()
        logger.info(
            "user_provisioned_via_sso",
            user_id=str(user.id),
            provider=identity.provider,
        )
        return user

    async def _alloc_username(self, email: str) -> str:
        """Derive a unique username from the email local-part (collision-safe)."""
        local = (email.split("@")[0] or "user")[:32]
        if not await self._repo.get_by_username(local):
            return local
        for _ in range(5):
            candidate = f"{local}-{secrets.token_hex(3)}"
            if not await self._repo.get_by_username(candidate):
                return candidate
        raise AuthenticationError("Could not allocate a username for the SSO account.")

    async def verify_email(self, token: str) -> User:
        """Mark a user's email verified via a signed verification token."""
        payload = decode_token(token)
        if payload.get("type") != "verify_email":
            raise AuthenticationError("Invalid or expired verification token.")
        user_id = self._user_id_from_payload(payload)
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise AuthenticationError("User not found.")
        user.is_verified = True
        await self._repo.update(user, {})
        return user

    async def send_verification_email(self, user: User) -> dict:
        token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.EMAIL_VERIFY_TOKEN_MINUTES),
            token_type="verify_email",
        )
        link = build_email_link("/verify-email?token=", token)
        result = await email_service.send(
            to=user.email,
            subject="Verify your Nexus account",
            body_html=f"<p>Welcome, {user.username}!</p><p><a href='{link}'>Verify your email</a></p>",
            body_text=f"Verify your email: {link}",
        )
        result["dev_link"] = link if result.get("channel") == "dev" else None
        return result

    async def initiate_password_reset(self, email: str) -> dict:
        """Issue + email a password reset token (never reveals whether the account exists)."""
        user = await self._repo.get_by_email(email)
        if not user:
            # Uniform response: don't leak account existence.
            return {"sent": False, "channel": "noop"}
        token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.PASSWORD_RESET_TOKEN_MINUTES),
            token_type="reset_password",
        )
        link = build_email_link("/reset-password?token=", token)
        result = await email_service.send(
            to=user.email,
            subject="Reset your Nexus password",
            body_html=f"<p>Reset your password: <a href='{link}'>link</a></p><p>This link expires in {settings.PASSWORD_RESET_TOKEN_MINUTES} minutes.</p>",
            body_text=f"Reset your password: {link}",
        )
        result["dev_link"] = link if result.get("channel") == "dev" else None
        return result

    async def reset_password(self, token: str, new_password: str) -> User:
        payload = decode_token(token)
        if payload.get("type") != "reset_password":
            raise AuthenticationError("Invalid or expired password reset token.")
        user_id = self._user_id_from_payload(payload)
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise AuthenticationError("User not found.")
        user.hashed_password = get_password_hash(new_password)
        user.is_verified = True
        await self._repo.update(user, {})
        logger.info("password_reset_completed", user_id=str(user_id))
        return user

    @staticmethod
    def _user_id_from_payload(payload: dict) -> UUID:
        sub_str = payload.get("sub")
        try:
            return UUID(str(sub_str))
        except (ValueError, TypeError):
            raise AuthenticationError("Invalid token subject.")

    async def refresh(self, refresh_token_str: str) -> TokenResponse:
        """Issue a new access token from a valid, single-use refresh token."""
        payload = decode_token(refresh_token_str)
        if payload.get("type") != "refresh":
            raise AuthenticationError("Invalid or expired refresh token.")

        jti = payload.get("jti")
        if not jti:
            raise AuthenticationError("Invalid refresh token.")

        # Single-use guard: mark this refresh token jti as consumed in Redis.
        # If it was already redeemed, refuse the exchange (reuse detection).
        ttl_seconds = max(1, int(payload["exp"]) - int(time.time()))
        first_use = await get_cache_service().set_if_absent(
            f"refresh:used:{jti}", "1", ttl_seconds=ttl_seconds
        )
        if not first_use:
            raise AuthenticationError("Refresh token has already been used.")

        sub_str = payload.get("sub")
        if not sub_str:
            raise AuthenticationError("Invalid token subject.")
        try:
            user_id = UUID(str(sub_str))
        except (ValueError, TypeError):
            raise AuthenticationError("Invalid user ID in token.")

        user = await self._repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive.")

        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            additional_claims={"role": user.role, "email": user.email},
        )
        new_refresh = create_refresh_token(subject=user.id)
        return TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            token_type="bearer",  # nosec B106
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    async def revoke_refresh_token(self, refresh_token_str: str) -> None:
        """Revoke a refresh token by recording its jti in Redis until its expiration."""
        try:
            payload = decode_token(refresh_token_str)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                ttl_seconds = max(1, int(exp) - int(time.time()))
                await get_cache_service().set(f"refresh:used:{jti}", "1", ttl_seconds=ttl_seconds)
        except Exception:  # nosec B110
            # Best-effort revocation: expired or malformed tokens cannot be re-used anyway
            pass
