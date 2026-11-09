"""
Authentication API Router.
Pure HTTP transport layer — delegates all auth use cases to AuthService (SRP + DIP).
"""
import hashlib
import time
from collections import deque
from urllib.parse import quote

import structlog
from backend.app.api.deps import get_auth_service, get_current_user, get_db, get_oauth_service
from backend.app.core.client_ip import resolve_client_ip
from backend.app.core.config import settings
from backend.app.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.app.core.security import decode_token
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    ForgotPasswordRequest,
    OAuthCallbackRequest,
    OAuthLoginResponse,
    OAuthProvidersResponse,
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
from backend.app.services.oauth_service import OAuthProviderRegistry, OAuthService
from backend.app.services.two_factor_service import TwoFactorService
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
from sqlmodel.ext.asyncio.session import AsyncSession

bearer_optional = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])
logger = structlog.get_logger(__name__)


def _account_key(value: str | None) -> str | None:
    """Hash an account identifier so no raw email/username reaches Redis keys.

    Returns None when there is nothing to key on, so the per-account bucket is
    skipped rather than collapsing every anonymous request onto one key.
    """
    if not value:
        return None
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:32]


# In-process sliding windows used when Redis is unreachable (see
# ``_enforce_auth_rate_limit``). Authentication must stay available during a
# Redis outage, but returning early -- as this did -- silently switched brute
# force protection *off* on login, register and password reset, which is the
# one moment an attacker needs it. Bounded: a flood of distinct buckets must not
# become a memory leak, so expired windows are swept and the coldest bucket is
# evicted before the cap is reached.
_LOCAL_RATE_WINDOWS: dict[str, deque[float]] = {}
_LOCAL_RATE_MAX_BUCKETS = 4096


def _memory_rate_limit(identifier: str, *, limit: int, window_seconds: int) -> bool:
    """Sliding-window check against this process's own memory. True = allowed.

    Counts the current attempt when it lets it through, mirroring the Redis
    path, so a bucket cannot be re-checked to gain an extra allowance.
    """
    now = time.monotonic()
    window = _LOCAL_RATE_WINDOWS.get(identifier)
    if window is None:
        if len(_LOCAL_RATE_WINDOWS) >= _LOCAL_RATE_MAX_BUCKETS:
            cutoff = now - window_seconds
            for stale in [
                k for k, w in _LOCAL_RATE_WINDOWS.items() if not w or w[-1] <= cutoff
            ]:
                del _LOCAL_RATE_WINDOWS[stale]
            if len(_LOCAL_RATE_WINDOWS) >= _LOCAL_RATE_MAX_BUCKETS:
                coldest = min(_LOCAL_RATE_WINDOWS, key=lambda k: _LOCAL_RATE_WINDOWS[k][-1])
                del _LOCAL_RATE_WINDOWS[coldest]
        window = deque()
        _LOCAL_RATE_WINDOWS[identifier] = window

    cutoff = now - window_seconds
    while window and window[0] <= cutoff:
        window.popleft()
    if len(window) >= limit:
        return False
    window.append(now)
    return True


async def _enforce_auth_rate_limit(
    request: Request,
    *,
    action: str,
    limit: int,
    account: str | None = None,
) -> None:
    """Apply per-client-IP and (optionally) per-account windows.

    The client address is resolved through ``resolve_client_ip`` so a forwarded
    header is honoured only from a configured trusted proxy -- reading
    ``X-Forwarded-For`` directly let a caller mint a fresh rate-limit key per
    request with a spoofed hop. The per-account bucket closes the remaining
    hole: an attacker with many source addresses still hits one cap per target
    account (finding: credential-stuffing / lockout-avoidance).
    """
    client_host = resolve_client_ip(request, settings.TRUSTED_PROXY_IPS)
    buckets = [f"auth:{action}:{client_host}"]
    key = _account_key(account)
    if key:
        buckets.append(f"auth:{action}:acct:{key}")
    for identifier in buckets:
        # The per-account bucket is a second key, not a second cap: both are
        # checked so neither a single noisy address nor a distributed attempt
        # on one account slips through.
        try:
            allowed, _remaining = await redis_service.check_rate_limit(
                identifier=identifier,
                limit=limit,
                window_seconds=60,
                cost=1,
            )
        except Exception as exc:
            # Authentication stays available during a Redis outage -- but the
            # old `return` here meant "no Redis, no limit", so brute-force
            # protection vanished exactly when it mattered. Fall back to this
            # process's own window instead: degraded (per-instance, not shared)
            # but never absent.
            allowed = _memory_rate_limit(identifier, limit=limit, window_seconds=60)
            logger.warning(
                "auth_rate_limit_unavailable",
                action=action,
                error_type=type(exc).__name__,
                fallback="in_process_window",
            )
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
    await _enforce_auth_rate_limit(request, action="register", limit=5, account=user_in.email)
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
    response: Response,
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_svc: AuthService = Depends(get_auth_service),
    _rl: None = rate_limit_anon("auth.login", limit=60, window_seconds=60),
):
    """
    Returns a token pair. When the user has TOTP 2FA enabled, returns
    ``{"status": "2fa_required", "preauth_token": ..., "expires_in": ...}``
    instead; exchange it via POST /auth/2fa/verify.

    Both tokens are set as httpOnly cookies (``nexus_access_token`` and
    ``nexus_refresh_token``). The response body carries no secrets.
    """
    await _enforce_auth_rate_limit(
        request, action="login", limit=10, account=form_data.username
    )
    try:
        tokens = await auth_svc.login(form_data.username, form_data.password)
    except AuthenticationError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=exc.message,
            headers={"WWW-Authenticate": "Bearer"},
        )

    # Set both tokens as httpOnly cookies. The frontend must never read them
    # from JavaScript; the BFF forwards cookies server-side.
    response.set_cookie(
        key="nexus_access_token",
        value=tokens.access_token,
        max_age=tokens.expires_in,
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key="nexus_refresh_token",
        value=tokens.refresh_token,
        max_age=tokens.refresh_expires_in,
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
        path="/",
    )
    return {"ok": True}


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    token_in: TokenRefresh | None = None,
    auth_svc: AuthService = Depends(get_auth_service),
):
    await _enforce_auth_rate_limit(request, action="refresh", limit=20)
    # Prefer the httpOnly cookie; fall back to the request body for clients
    # that still send it (backwards compatibility during rollout).
    refresh_token = request.cookies.get("nexus_refresh_token")
    if not refresh_token and token_in:
        refresh_token = token_in.refresh_token
    if not refresh_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="No refresh token"
        )
    try:
        tokens = await auth_svc.refresh(refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)

    # Rotate the refresh cookie with the new token.
    response.set_cookie(
        key="nexus_access_token",
        value=tokens.access_token,
        max_age=tokens.expires_in,
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
        path="/",
    )
    response.set_cookie(
        key="nexus_refresh_token",
        value=tokens.refresh_token,
        max_age=tokens.refresh_expires_in,
        httponly=True,
        secure=settings.ENVIRONMENT != "development",
        samesite="lax",
        path="/",
    )
    return tokens


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
    Revoke the session server-side and complete logout.

    The refresh token is taken from the request body, else the
    ``nexus_refresh_token`` cookie; if neither is present the server derives
    the token family from the access token's ``fam`` claim, so a client that
    sends only an Authorization header still terminates its 30-day session.
    Uses auto_error=False so an expired access token never blocks logging out.
    """
    await _enforce_auth_rate_limit(request, action="logout", limit=20)
    access_token = credentials.credentials if credentials and credentials.credentials else None
    refresh_token = token_in.refresh_token if token_in else None
    if not refresh_token:
        refresh_token = request.cookies.get("nexus_refresh_token")

    # Blacklist the current access token (by jti) so it dies immediately
    # instead of living out its TTL. Fail-open: a malformed/expired token or
    # an offline Redis must never prevent logout.
    if access_token:
        try:
            payload = decode_token(access_token)
            jti = payload.get("jti")
            if jti:
                exp = payload.get("exp")
                ttl_seconds = max(1, int(exp) - int(time.time())) if exp else 3600
                await get_cache_service().blacklist_token(str(jti), ttl_seconds)
        except Exception:  # nosec B110
            # revocation is best-effort; the token's exp still bounds reuse
            pass

    revoked = await auth_svc.logout(access_token=access_token, refresh_token=refresh_token)
    if not revoked:
        # Honest response for an unauthenticated/empty logout: previously this
        # returned "Logged out successfully" while revoking nothing.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No active session was found to revoke.",
        )
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
    request: Request,
    body: TwoFactorVerifyRequest,
    auth_svc: AuthService = Depends(get_auth_service),
):
    """Exchange a preauth challenge + valid TOTP code for the real token pair."""
    # A TOTP code is 6 digits and pyotp accepts a +/-1 step window, so only a
    # few codes are valid at any instant. Without this limit the endpoint is an
    # unthrottled oracle: an attacker holding a stolen preauth token could grind
    # codes until one lands.
    await _enforce_auth_rate_limit(
        request, action="2fa_verify", limit=10, account=body.preauth_token
    )
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
    request: Request,
    auth_svc: AuthService = Depends(get_auth_service),
):
    await _enforce_auth_rate_limit(request, action="verify_email", limit=10)
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
    request: Request,
    auth_svc: AuthService = Depends(get_auth_service),
):
    """Issues + emails a reset token. Returns the same envelope for known/unknown emails."""
    await _enforce_auth_rate_limit(
        request, action="forgot_password", limit=5, account=body.email
    )
    result = await auth_svc.initiate_password_reset(body.email)
    return result


@router.post("/reset-password", response_model=UserResponse)
async def reset_password(
    body: ResetPasswordRequest,
    request: Request,
    auth_svc: AuthService = Depends(get_auth_service),
):
    await _enforce_auth_rate_limit(
        request, action="reset_password", limit=5, account=body.token
    )
    try:
        return await auth_svc.reset_password(body.token, body.new_password)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


# ─── OAuth / OAuth (Google + GitHub) ─────────────────────────────────────────
# Authorization Code + PKCE, one adapter per provider. GET /oauth/{provider}
# returns the provider authorize URL; POST /oauth/{provider}/callback accepts
# the code the provider hands back (the frontend proxy performs the exchange
# and stores the session in httpOnly cookies). Both endpoints 404 while the
# named provider is unconfigured.

# Declared BEFORE `/oauth/{provider}`. FastAPI matches routes in registration
# order, so a literal path added after a `{provider}` catch-all is unreachable:
# the request for `/oauth/providers` is captured by the parameterised route and
# answered with the "not configured" 404 for a provider named "providers". That
# is a silently dead endpoint -- the sign-in page would get a 404 and fall back
# to showing no OAuth buttons, which looks identical to OAuth being unconfigured.
@router.get("/oauth/providers", response_model=OAuthProvidersResponse)
async def oauth_providers() -> OAuthProvidersResponse:
    """Which OAuth providers this deployment can actually complete a login with.

    Unauthenticated by necessity: the sign-in page has no session yet, and that
    is the only time it needs this. The response reveals only provider names and
    a boolean, so there is nothing here for an unauthenticated caller to abuse.
    """
    return OAuthProvidersResponse(providers=OAuthProviderRegistry.describe_providers())


@router.get("/oauth/{provider}", response_model=OAuthLoginResponse)
async def oauth_login(
    provider: str,
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


@router.post("/oauth/{provider}/callback", response_model=TokenResponse | TwoFactorChallengeResponse)
async def oauth_callback(
    request: Request,
    provider: str,
    body: OAuthCallbackRequest,
    auth_svc: AuthService = Depends(get_auth_service),
    oauth_svc: OAuthService = Depends(get_oauth_service),
):
    if not oauth_svc.enabled:
        raise HTTPException(status_code=404, detail="OAuth single sign-on is not configured.")
    # The callback mints a full session, so it needs the same per-IP ceiling as
    # password login — otherwise the IdP leg is a cheaper way to hammer the
    # provisioning path.
    await _enforce_auth_rate_limit(request, action="oauth_callback", limit=20)
    try:
        identity = await oauth_svc.complete_login(body.code, body.state)
        return await auth_svc.oauth_login(identity)
    except AuthenticationError as exc:
        raise HTTPException(status_code=400, detail=exc.message)


@router.get("/oauth/{provider}/callback")
async def oauth_callback_browser(
    request: Request,
    provider: str,
    code: str = "",
    state: str = "",
    auth_svc: AuthService = Depends(get_auth_service),
    oauth_svc: OAuthService = Depends(get_oauth_service),
):
    """
    Direct browser callback from IdPs (Google/GitHub redirect URI target).
    Exchanges code + state, sets httpOnly session cookies, and redirects to /app.
    """
    if not oauth_svc.enabled:
        raise HTTPException(status_code=404, detail="OAuth single sign-on is not configured.")
    # Where the browser lands after the callback. Never hardcode the dev origin:
    # a deployed instance would send every OAuth user to localhost:3000.
    frontend_origin = (settings.APP_PUBLIC_URL or "http://localhost:3000").rstrip("/")
    if not code or not state:
        return RedirectResponse(
            url=f"{frontend_origin}/signin?error=oauth_missing_params", status_code=302
        )

    await _enforce_auth_rate_limit(request, action="oauth_callback", limit=20)
    try:
        identity = await oauth_svc.complete_login(code, state)
        res = await auth_svc.oauth_login(identity)
        if isinstance(res, TwoFactorChallengeResponse):
            return RedirectResponse(url=f"{frontend_origin}/signin?oauth=2fa", status_code=302)

        response = RedirectResponse(url=f"{frontend_origin}/app", status_code=302)
        is_prod = settings.ENVIRONMENT == "production"
        response.set_cookie(
            key="nexus_access_token",
            value=res.access_token,
            max_age=res.expires_in or 3600,
            path="/",
            httponly=True,
            secure=is_prod,
            samesite="lax",
        )
        if res.refresh_token:
            response.set_cookie(
                key="nexus_refresh_token",
                value=res.refresh_token,
                max_age=res.refresh_expires_in or 2592000,
                path="/",
                httponly=True,
                secure=is_prod,
                samesite="lax",
            )
        return response
    except AuthenticationError as exc:
        # `quote`, not raw interpolation: the message contains spaces and `.`,
        # and a raw space in a Location header is not a valid URI.
        return RedirectResponse(
            url=f"{frontend_origin}/signin?error={quote(exc.message)}", status_code=302
        )
