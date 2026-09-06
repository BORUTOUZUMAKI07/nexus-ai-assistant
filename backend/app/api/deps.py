"""
FastAPI Dependencies for Authentication, Database, Rate Limiting,
Infrastructure Services, and Application Services.
"""
from collections.abc import AsyncGenerator
from uuid import UUID

from backend.app.core.config import settings
from backend.app.core.security import decode_token
from backend.app.domain.user.models import User
from backend.app.domain.user.repository import UserRepository
from backend.app.infrastructure.cache.base import ICacheService
from backend.app.infrastructure.cache.redis_client import get_cache_service
from backend.app.infrastructure.database.session import get_db_session
from backend.app.infrastructure.storage.base import IStorageService

# --------------------------------------------------------------------------- #
# Infrastructure singletons (imported lazily to avoid circular imports)
# --------------------------------------------------------------------------- #
from backend.app.infrastructure.storage.supabase_storage import get_storage_service
from backend.app.infrastructure.vector.base import IVectorStore
from backend.app.infrastructure.vector.qdrant_client import get_vector_store
from fastapi import Depends, HTTPException, status
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
    payload = decode_token(token)
    if payload is None:
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


async def get_current_admin(
    current_user: User = Depends(get_current_user),
) -> User:
    if current_user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="The user doesn't have enough privileges",
        )
    return current_user


# --------------------------------------------------------------------------- #
# Infrastructure Dependency Providers (DIP: depend on abstractions)
# --------------------------------------------------------------------------- #

def get_storage() -> IStorageService:
    """Inject the active IStorageService implementation."""
    return get_storage_service()


def get_vector_db() -> IVectorStore:
    """Inject the active IVectorStore implementation."""
    return get_vector_store()


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

