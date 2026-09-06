"""
Authentication API Router.
Pure HTTP transport layer — delegates all auth use cases to AuthService (SRP + DIP).
"""
from backend.app.api.deps import get_auth_service, get_current_user
from backend.app.core.exceptions import AuthenticationError, UserAlreadyExistsError
from backend.app.domain.user.models import User
from backend.app.domain.user.schemas import (
    TokenRefresh,
    TokenResponse,
    UserCreate,
    UserResponse,
)
from backend.app.services.auth_service import AuthService
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm

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
