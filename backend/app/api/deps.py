"""
FastAPI Dependencies for Authentication, Database, Rate Limiting,
Infrastructure Services, and Application Services.
"""
from collections.abc import AsyncGenerator
from uuid import UUID

from backend.app.core.config import settings
from backend.app.core.exceptions import InvalidTokenError
from backend.app.core.security import decode_token
from backend.app.domain.user.models import User
from backend.app.domain.user.repository import UserRepository
from backend.app.infrastructure.cache.base import ICacheService
from backend.app.infrastructure.cache.redis_client import get_cache_service
from backend.app.infrastructure.database.session import get_db_session
from backend.app.infrastructure.resilience.guards import IdempotencyGuard
from backend.app.infrastructure.storage.base import IStorageService

# --------------------------------------------------------------------------- #
# Infrastructure singletons (imported lazily to avoid circular imports)
# --------------------------------------------------------------------------- #
from backend.app.infrastructure.storage.supabase_storage import get_storage_service
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from sqlmodel.ext.asyncio.session import AsyncSession

oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_V1_PREFIX}/auth/login")


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async for session in get_db_session():
        yield session


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_db),
) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = decode_token(token)
    except InvalidTokenError:
        raise credentials_exception
    if payload.get("type") != "access":
        raise credentials_exception

    # Reject access tokens that were blacklisted on logout. Fail-open when the
    # cache is unavailable so an offline Redis never bricks authentication.
    # NOTE: only the cache READ sits in the try — the raise must stay outside,
    # or the except would swallow the 401 it just produced.
    jti = payload.get("jti")
    if jti:
        is_blacklisted = False
        try:
            is_blacklisted = await get_cache_service().is_token_blacklisted(str(jti))
        except Exception:
            is_blacklisted = False
        if is_blacklisted:
            raise credentials_exception

    user_id_str: str = payload.get("sub")
    if user_id_str is None:
        raise credentials_exception

    try:
        user_id = UUID(user_id_str)
    except ValueError:
        raise credentials_exception

    user_repo = UserRepository(session)
    user = await user_repo.get_by_id(user_id)
    if user is None or not user.is_active:
        raise credentials_exception

    return user


async def get_current_org_id(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_db),
) -> UUID | None:
    """Resolve the caller's organization id (None for users without an org).

    Multi-tenant scoping: rate-limit keys fold this in for per-org ceilings
    and usage/cost telemetry is attributed to the org for per-org rollups.
    """
    from backend.app.services.org_service import OrganizationService

    return await OrganizationService(session).resolve_org_id(current_user.id)


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


def require_idempotency_key(scope: str):
    """FastAPI dependency enforcing client ``Idempotency-Key`` replay protection.

    HLD idempotency pattern: when the caller supplies an ``Idempotency-Key``
    header, the first request with that key is accepted and the key is marked
    for 24h; any replay of the same key answers 409 so a network retry can
    never fire a side-effecting endpoint (tool execute/approval, uploads) twice.
    No header → no-op (backward compatible). Fail-open: an unavailable cache
    lets the request through rather than blocking legitimate traffic.
    """

    async def dependency(
        request: Request,
        current_user: User = Depends(get_current_user),
        cache: ICacheService = Depends(get_cache),
    ) -> None:
        client_key = request.headers.get("Idempotency-Key")
        if not client_key:
            return
        allowed = await IdempotencyGuard(cache).check_and_mark(scope, str(current_user.id), client_key)
        if not allowed:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Duplicate request: this Idempotency-Key was already consumed.",
            )

    return Depends(dependency)


# --------------------------------------------------------------------------- #
# Infrastructure Dependency Providers (DIP: depend on abstractions)
# --------------------------------------------------------------------------- #

def get_storage() -> IStorageService:
    """Inject the active IStorageService implementation."""
    return get_storage_service()


def get_cache() -> ICacheService:
    """Inject the active ICacheService implementation."""
    return get_cache_service()


# --------------------------------------------------------------------------- #
# Application Service Dependency Providers
# --------------------------------------------------------------------------- #

def get_file_service(
    storage: IStorageService = Depends(get_storage),
) -> "FileService":
    """
    Inject a fully configured FileService with concrete infrastructure implementations.
    High-level routes depend on this service, not on raw infrastructure singletons.
    """
    from backend.app.services.file_service import FileService
    from backend.app.services.rag.ingest import ingestion_service
    return FileService(storage=storage, ingestion=ingestion_service)


def get_auth_service(session: AsyncSession = Depends(get_db)) -> "AuthService":
    """Inject AuthService with database session."""
    from backend.app.services.auth_service import AuthService
    return AuthService(session)


def get_oauth_service(request: Request) -> "OAuthService":
    """Inject the provider adapter for the route's ``{provider}`` path segment.

    Unknown provider names yield a never-enabled service, so the
    /auth/oauth/{provider}* routes answer 404 (exactly as they do for a
    registered provider whose client id is unconfigured) instead of guessing
    at a path.
    """
    from backend.app.services.oauth_service import OAuthService
    provider = str(request.path_params.get("provider", ""))
    return OAuthService.for_provider(provider)


def get_conversation_service(session: AsyncSession = Depends(get_db)) -> "ConversationService":
    """Inject ConversationService with database session."""
    from backend.app.services.conversation_service import ConversationService
    return ConversationService(session)


def get_user_settings_service(session: AsyncSession = Depends(get_db)) -> "UserSettingsService":
    """Inject UserSettingsService with database session."""
    from backend.app.services.user_settings_service import UserSettingsService
    return UserSettingsService(session)


def get_tool_service(session: AsyncSession = Depends(get_db)) -> "ToolService":
    """Inject ToolService with database session."""
    from backend.app.services.tool_service import ToolService
    return ToolService(session)


def get_prompt_service(session: AsyncSession = Depends(get_db)) -> "PromptService":
    """Inject PromptService with database session."""
    from backend.app.services.prompt_service import PromptService
    return PromptService(session)


def get_usage_service(session: AsyncSession = Depends(get_db)) -> "UsageService":
    """Inject UsageService with database session."""
    from backend.app.services.usage_service import UsageService
    return UsageService(session)


def get_account_service(session: AsyncSession = Depends(get_db)) -> "AccountService":
    """Inject AccountService (GDPR export/erasure) with database session."""
    from backend.app.services.account_service import AccountService
    return AccountService(session)


def get_org_service(session: AsyncSession = Depends(get_db)) -> "OrganizationService":
    """Inject OrganizationService with database session."""
    from backend.app.services.org_service import OrganizationService
    return OrganizationService(session)


def get_webhook_service(session: AsyncSession = Depends(get_db)) -> "WebhookService":
    """Inject WebhookService with database session."""
    from backend.app.services.webhook_service import WebhookService
    return WebhookService(session)


def get_share_service(session: AsyncSession = Depends(get_db)) -> "ShareService":
    """Inject ShareService with database session."""
    from backend.app.services.share_service import ShareService
    return ShareService(session)


def get_rag_service() -> "RAGService":
    """Inject RAGService with concrete RAG components (DIP)."""
    from backend.app.services.rag.citation import citation_service
    from backend.app.services.rag.reranking import reranker
    from backend.app.services.rag.retrieval import retrieval_service
    from backend.app.services.rag_service import RAGService
    return RAGService(
        retriever=retrieval_service,
        reranker=reranker,
        citation_service=citation_service,
    )


def get_plan_service(session: AsyncSession = Depends(get_db)) -> "PlanService":
    """Inject PlanService (plan-then-approve) with database session."""
    from backend.app.services.plan_service import PlanService
    return PlanService(session)


def get_artifact_service(session: AsyncSession = Depends(get_db)) -> "ArtifactService":
    """Inject ArtifactService (persisted, versioned artifacts) with database session."""
    from backend.app.services.artifact_service import ArtifactService
    return ArtifactService(session)


def get_hook_service(session: AsyncSession = Depends(get_db)) -> "HookService":
    """Inject HookService (lifecycle hook policy CRUD) with database session."""
    from backend.app.services.hook_service import HookService
    return HookService(session)

