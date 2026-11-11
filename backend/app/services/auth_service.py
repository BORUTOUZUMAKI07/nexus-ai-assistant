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
from backend.app.services.email_service import build_email_link, email_service, escape_html
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
# result before concluding the replay was a theft. Sub-window of
# REFRESH_REUSE_GRACE_SECONDS.
_REPLAY_PUBLISH_WAIT_SECONDS = 3.0
_REPLAY_POLL_SECONDS = 0.05

# The password-reset route only needs the mail-send to complete synchronously in
# local dev, where it returns a dev link the UI follows. In production the send
# is fire-and-forget so the known-address and unknown-address branches take
# comparable time (a synchronous SMTP round trip is a timing oracle for account
# existence even when the response body is identical). Tasks are kept alive in a
# module-level set so the event loop cannot garbage-collect one mid-flight.
_EMAIL_TASKS: set["asyncio.Task[None]"] = set()


def _dispatch_email(coro) -> None:  # type: ignore[no-untyped-def]
    """Run a mail coroutine in the background without awaiting it."""
    try:
        task = asyncio.create_task(coro)
    except RuntimeError:  # pragma: no cover - no running loop (sync test)
        coro.close()
        return
    _EMAIL_TASKS.add(task)
    task.add_done_callback(_EMAIL_TASKS.discard)


def _optional_user_id(payload: dict) -> UUID | None:
    """Parse ``sub`` into a UUID, returning None instead of raising.

    Logout must not fail on a malformed token -- it should fall through to the
    next identity source (or report that nothing was revoked).
    """
    try:
        return UUID(str(payload.get("sub")))
    except (ValueError, TypeError):
        return None


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
        # A successful sign-in proves the owner is present, so it must lift any
        # standing revocation for this account. Without this, a *user-wide* marker
        # written by a password reset or a logout-with-no-family would survive the
        # new sign-in and 401 every subsequent refresh for its full TTL -- the user
        # logs in fine and is then bounced back to the login page on the next
        # rotation, on every device, for REFRESH_TOKEN_EXPIRE_DAYS.
        await self._clear_refresh_family_revocation(user.id, family_id=family_id)
        access_token = create_access_token(
            subject=user.id,
            role=user.role,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            # `fam` binds the access token to its refresh family so logout can
            # terminate the session server-side even when the client presents
            # only an Authorization header (no body, no refresh cookie).
            additional_claims={"email": user.email, "fam": family_id},
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

    async def oauth_login(self, identity: OAuthIdentity) -> TokenResponse | TwoFactorChallengeResponse:
        """Sign a user in via a verified OIDC identity.

        Identity resolution order is deliberate: the IdP's stable subject id is
        the authoritative key, and the email is a linking fallback only. Once
        linked, later logins resolve by subject alone, so a provider that later
        reassigns or recycles an address cannot quietly re-point the account.

        An account already bound to a *different* provider is still reachable
        through this verified email, but the binding is left untouched: the
        columns hold one identity, and overwriting it would lock the original
        provider out. See the branch below for the trade-off.

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
            # This IdP identity has never been seen, so the email is the only
            # remaining join key. Two cases:
            #   * the account has no OAuth link yet -> adopt it and bind this
            #     identity, which is what makes subject-first resolution work
            #     from the next login onwards;
            #   * it is already bound to another provider -> sign in as that
            #     account WITHOUT rebinding. See the branch below for why the
            #     old refusal was wrong and what it costs.
            existing = await self._repo.get_by_email(email)
            if existing is not None:
                if existing.oauth_sub is not None:
                    # The account already has a provider of record; this login
                    # arrives through a *different* one that has verified the
                    # same address. Refusing it stranded every user who signed
                    # up with Google and later pressed GitHub: the message told
                    # them the method they were already using, and there is no
                    # in-product way to move the link.
                    #
                    # So resolve the account by the verified address and do NOT
                    # rebind: the stored (provider, subject) pair stays the one
                    # of record, because one column pair cannot represent two
                    # identities and silently overwriting it would lock the
                    # first provider out. The trust this grants is the trust a
                    # password reset already grants -- the caller proved control
                    # of the account's own address at an identity provider --
                    # and it is logged as a distinct event so the weaker path is
                    # visible in the audit trail rather than indistinguishable.
                    logger.info(
                        "oauth_login_via_alias_provider",
                        user_id=str(existing.id),
                        linked_provider=existing.oauth_provider,
                        alias_provider=identity.provider,
                    )
                    user = existing
                else:
                    existing.oauth_provider = identity.provider
                    existing.oauth_sub = identity.subject
                    await self._repo.update(existing, {})
                    user = existing
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
            logger.info("two_factor_challenge_issued", user_id=str(user.id), via="oauth")
            return TwoFactorChallengeResponse(
                status="2fa_required",
                preauth_token=preauth,
                expires_in=settings.TOTP_PREAUTH_MINUTES * 60,
            )

        return await self._issue_token_pair(user)

    async def _provision_oauth_user(self, identity: OAuthIdentity, email: str) -> User:
        """Auto-provision a passwordless OAuth account (the IdP vouches for it)."""
        user = User(
            email=email,
            username=await self._alloc_username(email),
            hashed_password=None,  # OAuth accounts have no password
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
        # creates, so OAuth accounts behave identically downstream.
        self._session.add(UserSettings(user_id=user.id))
        await self._session.commit()
        logger.info(
            "user_provisioned_via_oauth",
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
        raise AuthenticationError("Could not allocate a username for the OAuth account.")

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
        # ``user.username`` is attacker-controlled at registration, so it is
        # escaped before it reaches the HTML body (stored HTML injection).
        result = await email_service.send(
            to=user.email,
            subject="Verify your Nexus account",
            body_html=(
                f"<p>Welcome, {escape_html(user.username)}!</p>"
                f"<p><a href='{link}'>Verify your email</a></p>"
            ),
            body_text=f"Verify your email: {link}",
        )
        # The token is surfaced to the caller only behind the explicit,
        # default-off dev-link setting: an HTTP response is not a delivery
        # channel a deployment should hand credentials through.
        result["dev_link"] = (
            link
            if settings.EMAIL_DEV_LINK_ENABLED and result.get("channel") == "dev"
            else None
        )
        return result

    async def initiate_password_reset(self, email: str) -> dict:
        """Issue + email a password reset token.

        The response is a fixed envelope, identical whether or not the address
        has an account: the mail-service result is discarded rather than
        returned, because its ``channel`` used to distinguish the two branches
        and ``dev_link`` handed the reset credential straight to the caller.
        When the account exists the token reaches the user only through the
        configured mail channel.
        """
        # The key is present in *every* response (value ``None`` unless the
        # dev-link setting is on), so its presence is not itself an existence
        # oracle and no branch can hand a credential through an HTTP response.
        envelope: dict[str, object] = {"sent": True, "channel": "email", "dev_link": None}

        user = await self._repo.get_by_email(email)
        if not user:
            return envelope

        token = create_access_token(
            subject=user.id,
            expires_delta=timedelta(minutes=settings.PASSWORD_RESET_TOKEN_MINUTES),
            # JWT `type` claim, not a secret.
            token_type="reset_password",  # nosec B106
            additional_claims={
                # Password-reset fingerprint: bind the token to the current
                # password hash. If the password changes after the token is
                # issued (e.g. the user logs in elsewhere and rotates), the
                # captured reset link becomes invalid -- an attacker who
                # intercepted the email cannot use it after the owner rotates.
                "pw_hash": user.hashed_password[:16],
            },
        )
        link = build_email_link("/reset-password?token=", token)
        send_coro = email_service.send(
            to=user.email,
            subject="Reset your Nexus password",
            body_html=(
                f"<p>Reset your password: <a href='{link}'>link</a></p>"
                f"<p>This link expires in {settings.PASSWORD_RESET_TOKEN_MINUTES} minutes.</p>"
            ),
            body_text=f"Reset your password: {link}",
        )
        if settings.EMAIL_DEV_LINK_ENABLED:
            envelope["dev_link"] = link
            await send_coro
        else:
            # Fire-and-forget. Awaiting a synchronous SMTP round trip on the
            # known-address branch only is a timing oracle for account
            # existence even when the response body is byte-identical.
            _dispatch_email(send_coro)
        return envelope

    async def reset_password(self, token: str, new_password: str) -> User:
        payload = decode_token(token)
        if payload.get("type") != "reset_password":
            raise AuthenticationError("Invalid or expired password reset token.")
        # Single-use: a reset token that leaked (log, proxy, shared link) must
        # not be replayable within its lifetime.
        jti = payload.get("jti")
        if jti and not await self._consume_reset_token(str(jti), payload):
            raise AuthenticationError("This password reset link has already been used.")
        user_id = self._user_id_from_payload(payload)
        user = await self._repo.get_by_id(user_id)
        if not user:
            raise AuthenticationError("User not found.")
        # Password-reset fingerprint: the token was bound to the password hash
        # at issue time. If the password has changed since, the captured link
        # is invalid -- an intercepted reset email cannot be used after the
        # owner rotates their password through another session.
        pw_hash = payload.get("pw_hash")
        if pw_hash and user.hashed_password[:16] != pw_hash:
            raise AuthenticationError(
                "This password reset link is stale -- your password was changed since it was issued."
            )
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

        Rotation is atomic via a Lua script in Redis: the single ``SETNX`` on
        ``refresh:used:{jti}`` guarantees exactly one winner, eliminating the
        race window between claiming the jti and publishing the replay response.
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

        ttl_seconds = max(1, int(payload["exp"]) - int(time.time()))

        # Do the idempotent work first (user lookup + token creation). If we lose
        # the atomic rotation race, we discard these and return the cached
        # response instead -- no harm done.
        user = await self._repo.get_by_id(user_id)
        if not user or not user.is_active:
            raise AuthenticationError("User not found or inactive.")

        access_token = create_access_token(
            subject=user.id,
            role=user.role,
            expires_delta=timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
            additional_claims={
                "email": user.email,
                **({"fam": family_id} if family_id else {}),
            },
        )
        new_refresh = create_refresh_token(
            subject=user.id, family_id=family_id or None
        )
        response = TokenResponse(
            access_token=access_token,
            refresh_token=new_refresh,
            token_type="bearer",  # nosec B106 - auth scheme, not a credential
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            refresh_expires_in=settings.REFRESH_TOKEN_EXPIRE_DAYS * 86400,
        )

        race_response_json = json.dumps(
            {
                "access_token": response.access_token,
                "refresh_token": response.refresh_token,
                "expires_in": response.expires_in,
                "refresh_expires_in": response.refresh_expires_in,
            }
        )

        # Atomic rotation: exactly one caller wins the SETNX on refresh:used:{jti}.
        result = await get_cache_service().rotate_refresh_token(
            jti=jti,
            ttl_seconds=ttl_seconds,
            grace_window_seconds=settings.REFRESH_REUSE_GRACE_SECONDS,
            race_response=race_response_json,
        )
        action = result["action"]

        if action == "first_use":
            # We won the rotation. Our tokens are the authoritative ones.
            return response

        if action == "race_replay":
            # We lost the race but the winner published its response within the
            # grace window. Return it -- this is a duplicate tab/retry, not theft.
            cached = json.loads(result["data"])
            logger.info(
                "refresh_replay_within_grace_window",
                user_id=str(user_id),
                jti=jti,
            )
            return TokenResponse(
                access_token=cached["access_token"],
                refresh_token=cached["refresh_token"],
                token_type="bearer",  # nosec B106 - auth scheme, not a credential
                expires_in=cached["expires_in"],
                refresh_expires_in=cached["refresh_expires_in"],
            )

        # action == "theft": jti already used, no race response cached. This is
        # a captured token presented outside the grace window -- treat as theft.
        logger.warning(
            "refresh_token_reuse_detected",
            user_id=str(user_id),
            jti=jti,
            has_family=bool(family_id),
        )
        if settings.REFRESH_REVOKE_ON_REUSE:
            await self._revoke_refresh_family(user_id, family_id=family_id or None)
        raise AuthenticationError("Refresh token has already been used.")

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

    async def _clear_refresh_family_revocation(
        self, user_id: UUID, family_id: str | None = None
    ) -> None:
        """Lift a standing refresh-family revocation on a successful sign-in.

        The counterpart to ``_revoke_refresh_family``: a sign-in is proof that the
        owner is present, so a user-wide marker left by a password reset or by a
        logout whose family could not be resolved must not survive it. Clearing
        the freshly minted family key as well is belt-and-braces -- a new hex
        family cannot collide, but the two keys are always cleared together so a
        future caller that reuses a family id cannot resurrect a revoked one.

        Best-effort: an unreachable cache logs and returns, because a failed
        clear must not turn a valid sign-in into an error.
        """
        keys = [f"{_REFRESH_REVOKED_PREFIX}{user_id}"]
        if family_id:
            keys.append(f"{_REFRESH_REVOKED_PREFIX}{family_id}")
        try:
            for key in keys:
                await get_cache_service().delete(key)
        except Exception as exc:  # pragma: no cover - defensive
            logger.warning(
                "refresh_family_clear_failed",
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

    async def revoke_refresh_family(
        self, user_id: UUID, family_id: str | None = None
    ) -> None:
        """Terminate a sign-in session server-side.

        Used by logout: the client may send no refresh token at all (only an
        Authorization header), so the server derives the family from the access
        token's ``fam`` claim and revokes it -- otherwise an explicit sign-out
        leaves the 30-day refresh token alive.
        """
        await self._revoke_refresh_family(user_id, family_id=family_id or None)

    async def revoke_all_sessions(self, user_id: UUID) -> None:
        """Revoke every refresh family for a user (used when no family is known)."""
        await self._revoke_refresh_family(user_id, family_id=None)

    async def logout(
        self, access_token: str | None, refresh_token: str | None
    ) -> bool:
        """Terminate the caller's session server-side.

        Resolves the refresh family from the refresh token when one is
        supplied, otherwise from the access token's ``fam`` claim. Returns
        ``True`` when a session was actually terminated and ``False`` when
        nothing resolvable was presented, so the route can tell the caller its
        logout did nothing rather than reporting a success it cannot honour.
        """
        user_id: UUID | None = None
        family_id: str | None = None

        if refresh_token:
            try:
                payload = decode_token(refresh_token)
                if payload.get("type") == "refresh":
                    user_id = _optional_user_id(payload)
                    family_id = str(payload.get("fam") or "") or None
                    jti = payload.get("jti")
                    exp = payload.get("exp")
                    if jti and exp:
                        ttl = max(1, int(exp) - int(time.time()))
                        try:
                            await get_cache_service().set(
                                f"refresh:used:{jti}", "1", ttl_seconds=ttl
                            )
                        except Exception as exc:
                            logger.warning(
                                "logout_refresh_mark_failed",
                                error=type(exc).__name__,
                            )
            except Exception:
                user_id = None

        if user_id is None and access_token:
            try:
                payload = decode_token(access_token)
                if payload.get("type") == "access":
                    user_id = _optional_user_id(payload)
                    family_id = str(payload.get("fam") or "") or None
            except Exception:
                user_id = None

        if user_id is None:
            return False
        # Family when known (one sign-in); user-wide otherwise, so a session
        # whose family cannot be determined still dies with the logout.
        await self._revoke_refresh_family(user_id, family_id=family_id)
        logger.info(
            "user_logged_out",
            user_id=str(user_id),
            family_id=family_id or "user-wide",
        )
        return True

    async def _consume_reset_token(self, jti: str, payload: dict) -> bool:
        """Mark a password-reset token single-use. False means already consumed.

        Fails open (returns True) when the cache is unavailable: an offline
        Redis must not make password reset impossible, and the token's ``exp``
        still bounds its life.
        """
        exp = payload.get("exp")
        ttl_seconds = (
            max(1, int(exp) - int(time.time())) if exp else 3600
        )
        try:
            return await get_cache_service().set_if_absent(
                f"reset:used:{jti}", "1", ttl_seconds=ttl_seconds
            )
        except Exception as exc:
            logger.warning(
                "reset_token_single_use_check_failed", error=type(exc).__name__
            )
            return True
