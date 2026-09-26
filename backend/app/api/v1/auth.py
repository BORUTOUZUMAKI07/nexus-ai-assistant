"""
Authentication API Router.
Pure HTTP transport layer — delegates all auth use cases to AuthService (SRP + DIP).
"""
from backend.app.api.deps import get_auth_service, get_current_user
from backend.app.infrastructure.cache.redis_client import redis_service
import structlog
from backend.app.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from backend.app.services.auth_service import AuthService
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm

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
        return await auth_svc.register(user_in)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=exc.message)


@router.post("/login", response_model=TokenResponse)
async def login(
    request: Request,
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_svc: AuthService = Depends(get_auth_service),
):
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
    auth_svc: AuthService = Depends(get_auth_service),
):
    try:
        return await auth_svc.refresh(token_in.refresh_token)
    except AuthenticationError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=exc.message)


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@router.post("/logout", status_code=status.HTTP_200_OK)
async def logout(
    token_in: TokenRefresh | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_optional),
    auth_svc: AuthService = Depends(get_auth_service),
):
    """
    Revoke refresh token in Redis and complete logout.
    Uses auto_error=False so expired access tokens never block logging out.
    """
    if token_in and token_in.refresh_token:
        await auth_svc.revoke_refresh_token(token_in.refresh_token)
    return {"message": "Logged out successfully"}
