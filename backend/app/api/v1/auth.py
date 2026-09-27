"""
Authentication API Router.
Pure HTTP transport layer — delegates all auth use cases to AuthService (SRP + DIP).
"""
import time

import structlog
from backend.app.api.deps import get_auth_service, get_current_user, get_db, get_oauth_service
from backend.app.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.app.core.security import decode_token
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    ForgotPasswordRequest,
    OAuthCallbackRequest,
    OAuthLoginResponse,
    ResetPasswordRequest,
    TokenRefresh,
    TokenResponse,
    TwoFactorChallengeResponse,
    TwoFactorCodeRequest,
    TwoFactorSetupResponse,
    TwoFactorVerifyRequest,
    UserCreate,
    UserResponse,
    VerifyEmailRequest,
)
from backend.app.infrastructure.cache.redis_client import get_cache_service, redis_service
from backend.app.infrastructure.resilience.rate_limit import rate_limit_anon
from backend.app.services.auth_service import AuthService
from backend.app.services.oauth_service import OAuthService
from backend.app.services.two_factor_service import TwoFactorService
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession

bearer_optional = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = structlog.get_logger(__name__)


async def _enforce_auth_rate_limit(request: Request, *, action: str, limit: int) -> None:
    """Apply a per-client-IP window without trusting unvalidated forwarded headers."""
    client_host = request.client.host if request.client else "unknown"
    try:
        allowed, _remaining = await redis_service.check_rate_limit(
            identifier=f"auth:{action}:{client_host}",
            limit=limit,
            window_seconds=60,
            cost=1,
        )
    except Exception as exc:
        # Keep authentication available during Redis outages; never log credentials.
        logger.warning("auth_rate_limit_unavailable", action=action, error_type=type(exc).__name__)
        return
    if not allowed:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many authentication attempts. Please try again in a minute.",
            headers={"Retry-After": "60"},
        )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_in: UserCreate,
    request: Request,
    auth_svc: AuthService = Depends(get_auth_service),
):
    await _enforce_auth_rate_limit(request, action="register", limit=5)
    try:
        user = await auth_svc.register(user_in)
        # Best-effort verification email ("notify" leg of email infra).
        try:
            await auth_svc.send_verification_email(user)
        except Exception:  # nosec B110
            # notify leg is best-effort; never block signup
            pass
        return user
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=exc.message)


@router.post("/login")
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_svc: AuthService = Depends(get_auth_service),
    _rl: None = rate_limit_anon("auth.login", limit=60, window_seconds=60),
):
    """
    Returns a token pair. When the user has TOTP 2FA enabled, returns
    ``{"status": "2fa_required", "preauth_token": ..., "expires_in": ...}``
    instead; exchange it via POST /auth/2fa/verify.
    """
    await _enforce_auth_rate_limit(request, action="login", limit=10)
    try:
        return await auth_svc.login(form_data.username, form_data.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    token_in: TokenRefresh,
    request: Request,
    auth_svc: AuthService = Depends(get_auth_service),
):
    await _enforce_auth_rate_limit(request, action="refresh", limit=20)
    try:
        return await auth_svc.refresh(token_in.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    request: Request,
    token_in: TokenRefresh | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    auth_svc: AuthService = Depends(get_auth_service),
):
    """
    Revoke refresh token in Redis and complete logout.
    Uses auto_error=False so expired access tokens never block logging out.
    """
    await _enforce_auth_rate_limit(request, action="logout", limit=20)
    # Blacklist the current access token (by jti) so it dies immediately
    # instead of living out its TTL. Fail-open: a malformed/expired token or
    # an offline Redis must never prevent logout.
    if credentials and credentials.credentials:
        try:
            payload = decode_token(credentials.credentials)
            jti = payload.get("jti")
            if jti:
                exp = payload.get("exp")
                ttl_seconds = max(1, int(exp) - int(time.time())) if exp else 3600
                await get_cache_service().blacklist_token(str(jti), ttl_seconds)
        except Exception:  # nosec B110
            # revocation is best-effort; the token's exp still bounds reuse
            pass
    if token_in and token_in.refresh_token:
        await auth_svc.revoke_refresh_token(token_in.refresh_token)
    return {"message": "Logged out successfully"}


# ─── Two-Factor Authentication (TOTP) ────────────────────────────────────────

@router.post("/2fa/setup", response_model=TwoFactorSetupResponse)
async def two_factor_setup(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Provision TOTP: returns the secret + otpauth URI. Not enabled until /2fa/enable."""
    svc = TwoFactorService(session)
    try:
        return await svc.setup(current_user.id, current_user.email)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc))


@router.post("/2fa/enable")
async def two_factor_enable(
    body: TwoFactorCodeRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Confirm the TOTP code from setup and enable 2FA for this account."""
    svc = TwoFactorService(session)
    try:
        ok = await svc.enable(current_user.id, body.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid two-factor authentication code.")
    return {"status": "enabled"}


@router.post("/2fa/disable")
async def two_factor_disable(
    body: TwoFactorCodeRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    """Disable 2FA after validating the current TOTP code."""
    svc = TwoFactorService(session)
    try:
        ok = await svc.disable(current_user.id, body.code)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    if not ok:
        raise HTTPException(status_code=400, detail="Invalid two-factor authentication code.")
    return {"status": "disabled"}


@router.post("/2fa/verify", response_model=TokenResponse)
async def two_factor_verify(
    body: TwoFactorVerifyRequest,
    auth_svc: AuthService = Depends(get_auth_service),
):
    """Exchange a preauth challenge + valid TOTP code for the real token pair."""
    try:
        return await auth_svc.verify_2fa(body.preauth_token, body.code)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)


@router.get("/2fa/status")
async def two_factor_status(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
):
    svc = TwoFactorService(session)
    return {"enabled": await svc.is_enabled(current_user.id)}


# ─── Email verification & password reset ─────────────────────────────────────

@router.post("/verify-email", response_model=UserResponse)
async def verify_email(
    body: VerifyEmailRequest,
    auth_svc: AuthService = Depends(get_auth_service),
):
    try:
        return await auth_svc.verify_email(body.token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


@router.post("/resend-verification")
async def resend_verification(
    current_user: User = Depends(get_current_user),
    auth_svc: AuthService = Depends(get_auth_service),
):
    result = await auth_svc.send_verification_email(current_user)
    return result


@router.post("/forgot-password")
async def forgot_password(
    body: ForgotPasswordRequest,
    auth_svc: AuthService = Depends(get_auth_service),
):
    """Issues + emails a reset token. Returns the same envelope for known/unknown emails."""
    result = await auth_svc.initiate_password_reset(body.email)
    return result


@router.post("/reset-password", response_model=UserResponse)
async def reset_password(
    body: ResetPasswordRequest,
    auth_svc: AuthService = Depends(get_auth_service),
):
    try:
        return await auth_svc.reset_password(body.token, body.new_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


# ─── OAuth / OIDC SSO ─────────────────────────────────────────────────────────
# Authorization Code + PKCE. GET /oauth/login returns the provider authorize
# URL; POST /oauth/callback accepts the code the provider hands back (the
# frontend proxy performs the exchange and stores the session in httpOnly
# cookies). Both endpoints are 404 while SSO is unconfigured.

@router.get("/oauth/login", response_model=OAuthLoginResponse)
async def oauth_login(
    oauth_svc: OAuthService = Depends(get_oauth_service),
):
    if not oauth_svc.enabled:
        raise HTTPException(status_code=404, detail="OAuth single sign-on is not configured.")
    authorization_url, state = await oauth_svc.create_authorization_url()
    return OAuthLoginResponse(
        authorization_url=authorization_url,
        state=state,
        provider=oauth_svc.provider,
    )


@router.post("/oauth/callback", response_model=TokenResponse | TwoFactorChallengeResponse)
async def oauth_callback(
    body: OAuthCallbackRequest,
    auth_svc: AuthService = Depends(get_auth_service),
    oauth_svc: OAuthService = Depends(get_oauth_service),
):
    if not oauth_svc.enabled:
        raise HTTPException(status_code=404, detail="OAuth single sign-on is not configured.")
    try:
        identity = await oauth_svc.complete_login(body.code, body.state)
        return await auth_svc.sso_login(identity)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)
