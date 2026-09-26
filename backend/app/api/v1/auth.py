"""
Authentication API Router.
Pure HTTP transport layer — delegates all auth use cases to AuthService (SRP + DIP).
"""
from backend.app.api.deps import get_auth_service, get_current_user
from backend.app.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.app.core.security import decode_token
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from backend.app.infrastructure.cache.redis_client import get_cache_service
from backend.app.services.auth_service import AuthService
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer, OAuth2PasswordRequestForm
import time

bearer_optional = HTTPBearer(auto_error=False)

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register_user(
    user_in: UserCreate,
    auth_svc: AuthService = Depends(get_auth_service),
):
    try:
        return await auth_svc.register(user_in)
    except UserAlreadyExistsError as exc:
        raise HTTPException(status_code=409, detail=exc.message)


@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    auth_svc: AuthService = Depends(get_auth_service),
):
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
        except Exception:
            pass
    if token_in and token_in.refresh_token:
        await auth_svc.revoke_refresh_token(token_in.refresh_token)
    return {"message": "Logged out successfully"}
