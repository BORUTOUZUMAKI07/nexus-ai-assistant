"""
Auth Application Service.
Owns all authentication and user registration use cases (SRP).
Route handlers depend on this abstraction, not on UserRepository directly (DIP).
"""
import asyncio
import json
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

# Presence of this key means "every refresh token for this user is revoked".
# Unlike the per-jti ``refresh:used:`` marker (which only blocks the one token
# that was replayed), this cascades: one replayed token locks out sibling
# sessions too, which is what turns a silent theft into a detectable event.
_REFRESH_REVOKED_PREFIX = "refresh:revoked:"

# How long a duplicate refresh waits for the in-flight winner to publish its
# tokens before concluding the replay was a theft, and how often it looks. The
# wait is a sub-window of REFRESH_REUSE_GRACE_SECONDS on purpose: the winner has
# one user lookup and one cache write left to do, so this is generous, but it
# still stops a stolen token from parking a request open for the whole window.
_REPLAY_PUBLISH_WAIT_SECONDS = 3.0
_REPLAY_POLL_SECONDS = 0.05


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
                # JWT `type` claim, not a secret.
                token_type="preauth",  # nosec B106
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

    async def _issue_token_pair(
        self, user: User, family_id: str | None = None
    ) -> TokenResponse:
        # A sign-in starts a new token family. A refresh carries the existing one
        # forward, so every token descended from one sign-in shares a family id and
        # a theft can be traced to the sign-in that leaked rather than to the user
        # as a whole. See refresh() for why that matters.
        family_id = family_id or secrets.token_hex(16)
        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            additional_claims={"role": user.role, "email": user.email},
        )
        refresh_token = create_refresh_token(
            subject=user.id, family_id=family_id
        )
        logger.info("user_logged_in", user_id=str(user.id))
        return TokenResponse(
            access_token=access_token,
            refresh_token=refresh_token,
            token_type="bearer",  # nosec B106
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

    async def sso_login(self, identity: OAuthIdentity) -> TokenResponse | TwoFactorChallengeResponse:
        """Sign a user in via a verified OIDC identity.

        Identity resolution order is deliberate: the IdP's stable subject id is
        the authoritative key, and the email is only a first-time linking
        fallback. Once linked, later logins resolve by subject alone, so a
        provider that later reassigns or recycles an address can never hand the
        address to a different person who would then inherit the account.
        TOTP-enabled accounts receive the same ``2fa_required`` preauth
        challenge as password login.
        """
        email = (identity.email or "").lower().strip()
        if not email or not identity.subject:
            raise AuthenticationError("OAuth provider returned an unusable identity.")
        if not identity.email_verified:
            # Defence in depth. ``OAuthService.complete_login`` already fails
            # closed on an unverified email claim, so arriving here with False
            # means this method was handed an identity that bypassed that gate.
            raise AuthenticationError("Provider has not verified this email.")

        user = await self._repo.get_by_oauth_identity(identity.provider, identity.subject)
        if user is None:
            # First time this IdP identity is seen: fall back to the email to
            # find a pre-existing (password) account to adopt.
            user = await self._repo.get_by_email(email)
            if user is not None and user.oauth_sub is None:
                user.oauth_provider = identity.provider
                user.oauth_sub = identity.subject
                await self._repo.update(user, {})
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
                # JWT `type` claim, not a secret.
                token_type="preauth",  # nosec B106
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
            # The caller has already established email_verified, so the
            # provisioned account is born trusted.
            is_verified=True,
            oauth_provider=identity.provider,
            oauth_sub=identity.subject,
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
            # JWT `type` claim, not a secret.
            token_type="verify_email",  # nosec B106
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
            # JWT `type` claim, not a secret.
            token_type="reset_password",  # nosec B106
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
        # A reset is a credential change: cut every existing refresh session so
        # a token captured before the reset cannot outlive it. Otherwise an
        # attacker holding a refresh token keeps access for the full
        # REFRESH_TOKEN_EXPIRE_DAYS window even after the owner resets.
        # No family id: a password reset must terminate *every* session the user
        # has, on every device. Scoping this to one sign-in would leave tokens
        # captured before the reset alive on the account's other sessions, which
        # is exactly what this call exists to prevent.
        await self._revoke_refresh_family(user.id)
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
        """Issue a new access token from a valid, single-use refresh token.

        Presenting the same token twice within ``REFRESH_REUSE_GRACE_SECONDS`` is
        treated as a race rather than a replay: two tabs expiring together both
        send the same cookie, and the caller is answered with the tokens the
        first exchange already issued. Outside that window it is a replay, and
        with ``REFRESH_REVOKE_ON_REUSE`` on (the default) it revokes the token's
        family -- one sign-in, not the user's other devices.
        """
        payload = decode_token(refresh_token_str)
        if payload.get("type") != "refresh":
            raise AuthenticationError("Invalid or expired refresh token.")

        jti = payload.get("jti")
        if not jti:
            raise AuthenticationError("Invalid refresh token.")

        sub_str = payload.get("sub")
        if not sub_str:
            raise AuthenticationError("Invalid token subject.")
        try:
            user_id = UUID(str(sub_str))
        except (ValueError, TypeError):
            raise AuthenticationError("Invalid user ID in token.")

        family_id = str(payload.get("fam") or "")

        if await self._is_refresh_family_revoked(user_id, family_id=family_id or None):
            raise AuthenticationError("Session has been revoked. Please sign in again.")

        # Single-use guard: mark this refresh token jti as consumed in Redis.
        # If it was already redeemed, refuse the exchange (reuse detection).
        ttl_seconds = max(1, int(payload["exp"]) - int(time.time()))
        first_use = await get_cache_service().set_if_absent(
            f"refresh:used:{jti}", "1", ttl_seconds=ttl_seconds
        )
        if not first_use:
            # This jti was already redeemed. Two very different situations look
            # identical here, and telling them apart is the whole problem:
            #
            #   * A race. The same cookie was sent twice within a second or two --
            #     two tabs waking together, or a client retrying. The first
            #     exchange already succeeded, so this one is redundant, not
            #     hostile. Serving the identical response again is correct and
            #     leaks nothing: the caller already holds this exact token.
            #   * A replay. A captured token presented later to establish a
            #     parallel session. That is theft and is treated as such.
            #
            # The window is what separates them. A genuine attacker racing the
            # owner inside the window is a far smaller risk than logging honest
            # users out of every device they own, which is what this did before.
            # The winner may still be mid-exchange -- it only writes its result
            # after the user lookup and signing. So losing the SETNX does not yet
            # mean the exchange finished; _replay_response waits briefly for the
            # in-flight winner to publish before we conclude anything.
            replayed = await self._replay_response(jti)
            if replayed is not None:
                logger.info(
                    "refresh_replay_within_grace_window",
                    user_id=str(user_id),
                    jti=jti,
                )
                return replayed

            # Never published, or published too long ago to be that same
            # exchange. Treat as theft.
            logger.warning(
                "refresh_token_reuse_detected",
                user_id=str(user_id),
                jti=jti,
                has_family=bool(family_id),
            )
            if settings.REFRESH_REVOKE_ON_REUSE:
                await self._revoke_refresh_family(user_id, family_id=family_id or None)
            raise AuthenticationError("Refresh token has already been used.")

        user = await self._repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive.")

        access_token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            additional_claims={"role": user.role, "email": user.email},
        )
        new_refresh = create_refresh_token(
            subject=user.id, family_id=family_id or None
        )
        response = TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            token_type="bearer",  # nosec B106
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

        # Publish the result so a racing duplicate of this very request can be
        # answered with the same tokens instead of being treated as a theft.
        # Only kept for the grace window, so a stolen token cannot lean on it
        # later.
        await self._store_replay_response(jti, response)
        return response

    async def _store_replay_response(
        self, jti: str, response: TokenResponse
    ) -> None:
        """Cache a rotation result for the length of the grace window."""
        window = settings.REFRESH_REUSE_GRACE_SECONDS
        if window <= 0:
            return
        try:
            await get_cache_service().set(
                f"refresh:race:{jti}",
                json.dumps(
                    {
                        "access_token": response.access_token,
                        "refresh_token": response.refresh_token,
                        "expires_in": response.expires_in,
                        "refresh_expires_in": response.refresh_expires_in,
                    }
                ),
                ttl_seconds=window,
            )
        except Exception as exc:  # pragma: no cover - defensive
            # A cache outage must not block a legitimate refresh. The cost is
            # that a racing duplicate is refused instead of served, which is the
            # behaviour that existed before the grace window existed at all.
            logger.warning(
                "refresh_race_cache_failed", jti=jti, error=type(exc).__name__
            )

    async def _replay_response(self, jti: str) -> TokenResponse | None:
        """Return the tokens an earlier exchange of this ``jti`` produced, or None.

        Losing the single-use guard does not tell us *when* the winning exchange
        happened -- it may still be running. So this polls for a bounded moment
        before giving up: the winner has only a user lookup and one cache write
        to do, which is milliseconds, but a slow database must not turn the
        duplicate into a logout. The bound is deliberately shorter than the grace
        window so a real attacker replaying a stolen token cannot park a request
        open for the full window and amplify load.

        None means the caller must treat the replay as a theft. The ``jti`` is
        unguessable and the entry is bound to that exact token, so this can only
        ever be answered by a duplicate of the request that wrote it.
        """
        window = settings.REFRESH_REUSE_GRACE_SECONDS
        if window <= 0:
            return None
        deadline = time.monotonic() + min(window, _REPLAY_PUBLISH_WAIT_SECONDS)
        while True:
            response = await self._read_replay_response(jti)
            if response is not None:
                return response
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(_REPLAY_POLL_SECONDS)

    async def _read_replay_response(self, jti: str) -> TokenResponse | None:
        """One non-blocking read of the published rotation result for ``jti``."""
        try:
            raw = await get_cache_service().get(f"refresh:race:{jti}")
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "refresh_race_cache_failed", jti=jti, error=type(exc).__name__
            )
            return None
        if not raw:
            return None
        try:
            data = json.loads(raw)
            return TokenResponse(
                access_token=data["access_token"],
                refresh_token=data["refresh_token"],
                token_type="bearer",  # nosec B106
                expires_in=data["expires_in"],
                refresh_expires_in=data["refresh_expires_in"],
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "refresh_race_cache_corrupt", jti=jti, error=type(exc).__name__
            )
            return None

    async def _revoke_refresh_family(
        self, user_id: UUID, family_id: str | None = None
    ) -> None:
        """Revoke a refresh-token family for the refresh-token lifetime.

        With a ``family_id`` the blast radius is one sign-in: the sessions that
        user opened on their phone and laptop survive a stolen token from the web
        tab. Without one -- a token minted before families existed -- it falls
        back to revoking every session for the user, which is the older, blunter
        behaviour and the safe direction to err in.

        Best-effort: if the cache is unreachable the marker is simply not
        written, so the replayed token still hits its own single-use rejection
        but sibling sessions survive. Never raises.
        """
        key = (
            f"{_REFRESH_REVOKED_PREFIX}{family_id}"
            if family_id
            else f"{_REFRESH_REVOKED_PREFIX}{user_id}"
        )
        try:
            await get_cache_service().set(
                key,
                "1",
                ttl_seconds=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "refresh_family_revoke_failed",
                user_id=str(user_id),
                family_id=family_id or "user-wide",
                error=type(exc).__name__,
            )

    async def _is_refresh_family_revoked(
        self, user_id: UUID, family_id: str | None = None
    ) -> bool:
        """Whether this refresh family was revoked. Fails open.

        Checks the family key when there is one, and the older user-wide key
        regardless, so a token that predates families still honours a revocation
        that was issued against the user.
        """
        keys = [f"{_REFRESH_REVOKED_PREFIX}{user_id}"]
        if family_id:
            keys.insert(0, f"{_REFRESH_REVOKED_PREFIX}{family_id}")
        try:
            for key in keys:
                if await get_cache_service().get(key):
                    return True
            return False
        except Exception as exc:
            # Fail open: an offline cache must never lock every user out.
            logger.warning(
                "refresh_family_check_failed",
                user_id=str(user_id),
                error=type(exc).__name__,
            )
            return False

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
