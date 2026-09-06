"""
Application Settings — loaded from environment / .env file.
All optional keys default to None (free-tier compatible).
"""
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=True,
        extra="ignore",
    )

    # ── App Identity ───────────────────────────────────────────────────────────
    ENVIRONMENT: str = Field(default="development", description="development | staging | production")
    PROJECT_NAME: str = Field(default="Nexus AI Assistant")
    APP_NAME: str = Field(default="Nexus AI Assistant")   # alias

    # ── API Routing ────────────────────────────────────────────────────────────
    API_V1_STR: str = Field(default="/api/v1")
    API_V1_PREFIX: str = Field(default="/api/v1")         # used in main.py include_router

    # ── Security ───────────────────────────────────────────────────────────────
    SECRET_KEY: str = Field(default="nexus-development-secret-key-min-32-chars-long-must-be-secure")
    JWT_SECRET_KEY: str = Field(default="nexus-development-secret-key-min-32-chars-long-must-be-secure")
    JWT_ALGORITHM: str = Field(default="HS256")
    ENCRYPTION_KEY: str = Field(default="nexus-aes256-key-32bytes-long!!")

    ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=60)
    REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=30)

    # ── CORS ───────────────────────────────────────────────────────────────────
    ALLOWED_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"]
    )
    CORS_ORIGINS: list[str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"]
    )

    # ── Database ───────────────────────────────────────────────────────────────
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev"
    )
    SUPABASE_URL: str | None = None
    SUPABASE_ANON_KEY: str | None = None
    SUPABASE_SERVICE_ROLE_KEY: str | None = None

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    REDIS_URL: str = Field(default="redis://localhost:6379/0")
    CELERY_BROKER_URL: str = Field(default="redis://localhost:6379/1")
    CELERY_RESULT_BACKEND: str = Field(default="redis://localhost:6379/2")

    # ── Qdrant ─────────────────────────────────────────────────────────────────
    QDRANT_URL: str = Field(default="http://localhost:6333")
    QDRANT_HOST: str = Field(default="localhost")
    QDRANT_PORT: int = Field(default=6333)
    QDRANT_API_KEY: str | None = None
    QDRANT_COLLECTION_NAME: str = Field(default="nexus_knowledge")

    # ── LLM Providers ──────────────────────────────────────────────────────────
    GROQ_API_KEY: str | None = None
    GEMINI_API_KEY: str | None = None
    OPENROUTER_API_KEY: str | None = None
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    TOGETHER_API_KEY: str | None = None

    # Default model aliases used across the app
    DEFAULT_MODEL: str = Field(default="groq/llama-3.3-70b-versatile")
    FAST_MODEL: str = Field(default="groq/llama-3.1-8b-instant")

    # ── Embeddings ─────────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = Field(default="BAAI/bge-small-en-v1.5")
    EMBEDDING_DIMENSION: int = Field(default=384)
    RERANKER_MODEL: str = Field(default="cross-encoder/ms-marco-MiniLM-L-6-v2")

    # ── External Services ──────────────────────────────────────────────────────
    E2B_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    FIRECRAWL_API_KEY: str | None = None
    HELICONE_API_KEY: str | None = None
    MEM0_API_KEY: str | None = None
    MEMORY_EXTRACTION_MODEL: str = Field(default="groq/llama-3.1-8b-instant")

    # ── Object Storage (Supabase Storage — uses SUPABASE_URL + SERVICE_ROLE_KEY) ─
    STORAGE_BUCKET: str = Field(default="nexus-knowledge", description="Supabase Storage bucket name")
    STORAGE_MAX_FILE_SIZE_MB: int = Field(default=50, description="Max file upload size in MB")

    # ── Observability (LangSmith & Sentry Free Tiers) ──────────────────────────
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = Field(default="nexus-ai-assistant")
    LANGSMITH_TRACING: bool = Field(default=False)
    SENTRY_DSN: str | None = None
    LOG_LEVEL: str = Field(default="INFO")

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    RATE_LIMIT_PER_MINUTE: int = Field(default=100)
    RATE_LIMIT_STREAM_COST: int = Field(default=5)

    # ── RAG ────────────────────────────────────────────────────────────────────
    RAG_TOP_K: int = Field(default=20)
    RAG_RERANK_TOP_N: int = Field(default=5)
    CHUNK_SIZE: int = Field(default=512)
    CHUNK_OVERLAP: int = Field(default=64)
    PARENT_CHUNK_SIZE: int = Field(default=512, description="Parent chunk target tokens")
    CHILD_CHUNK_SIZE: int = Field(default=128, description="Child chunk target tokens")
    CHILD_CHUNK_OVERLAP: int = Field(default=32, description="Child chunk overlap tokens")
    MAX_UPLOAD_SIZE_MB: int = Field(default=50)

    # ── Async Indexing (Celery) ───────────────────────────────────────────────
    # When True, /files/upload persists the file + DB row and dispatches
    # process_file_indexing_task to the Celery worker instead of ingesting
    # synchronously in the request. Falls back to sync ingest if the broker
    # is unreachable.
    ASYNC_INDEXING: bool = Field(default=False)

    # ── Self-Refinement (Critic Subagent) ─────────────────────────────────────
    CRITIC_MAX_REVISIONS: int = Field(default=2, description="Max revision passes of the critic subagent before a draft is accepted as-is")

    # ── Email (Free Gmail SMTP or Resend) ──────────────────────────────────────
    SMTP_HOST: str = Field(default="smtp.gmail.com")
    SMTP_PORT: int = Field(default=587)
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_TLS: bool = Field(default=True)
    RESEND_API_KEY: str | None = None
    EMAIL_FROM: str = Field(default="noreply@nexus-assistant.ai")

    # ── MCP ────────────────────────────────────────────────────────────────────
    MCP_SERVER_NAME: str = Field(default="nexus-mcp")
    MCP_SERVER_VERSION: str = Field(default="1.0.0")

    @field_validator("CORS_ORIGINS", "ALLOWED_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v):
        if isinstance(v, str):
            import json
            try:
                return json.loads(v)
            except Exception:
                return [origin.strip() for origin in v.split(",")]
        return v


settings = Settings()
