"""
Application Settings — loaded from environment / .env file.
All optional keys default to None (free-tier compatible).
"""
import logging
import secrets
from pathlib import Path

from dotenv import load_dotenv
from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)

# Load backend/.env into os.environ as well as into Settings. Several internal
# consumers read the process environment directly — mem0 (MEM0_API_KEY),
# LangSmith (LANGSMITH_API_KEY), and the LangGraph Postgres checkpointer
# (DATABASE_URL in orchestrator/graph.py) — and would otherwise miss the same
# values pydantic-settings loads below. `override=False` keeps real deployment
# env vars higher-priority than the local .env file.
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


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
    # No built-in defaults: secrets must come from the environment. In
    # non-production a random value is generated at boot; in production a
    # missing/placeholder value hard-fails startup (see _guard_prod_secrets).
    SECRET_KEY: str | None = Field(default=None, description="HMAC/JWT signing secret")
    JWT_SECRET_KEY: str | None = Field(default=None, description="Alias of SECRET_KEY")
    JWT_ALGORITHM: str = Field(default="HS256")
    ENCRYPTION_KEY: str | None = Field(default=None, description="AES-256 key material (≥32 bytes)")

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
    # Echo every SQL statement to the console. Off by default even in
    # development — on by default it floods the dev console with every
    # sqlalchemy query (pg_catalog table checks, etc.).
    DATABASE_ECHO: bool = Field(default=False)
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
    EMBEDDING_MODEL: str = Field(default="models/gemini-embedding-001")
    EMBEDDING_DIMENSION: int = Field(default=768)
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
    STORAGE_MAX_FILE_SIZE_MB: int = Field(default=50, description="Max file upload size in MB (Supabase bucket)")

    # ── Object Storage (S3 legacy aliases — fused from the old app/settings.py) ─
    SUPABASE_S3_ENDPOINT: str | None = None
    SUPABASE_S3_BUCKET: str = Field(default="nexus-knowledge")
    SUPABASE_S3_ACCESS_KEY_ID: str | None = None
    SUPABASE_S3_SECRET_ACCESS_KEY: str | None = None

    # ── Prompt templates ──────────────────────────────────────────────────────
    # Single source of truth, computed at import so the default works in any
    # checkout without duplication.
    PROMPT_DIR: str = Field(
        default="",
        description="Directory containing prompt template .txt files",
    )

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

    # ── HITL Approvals ────────────────────────────────────────────────────────
    HITL_APPROVAL_TIMEOUT_SECONDS: int = Field(default=900, description="How long a parked HITL approval stays valid before auto-expiring")

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
    # Shared secret accepted by the MCP endpoint (x-nexus-mcp-key header). When
    # set, both a valid Bearer JWT and this static key are accepted.
    MCP_API_KEY: str | None = Field(default=None)
    # When True (default) the /mcp endpoint rejects requests without valid auth.
    # Set to False only for unauthenticated local tooling; do NOT disable in prod.
    MCP_AUTH_ENABLED: bool = Field(default=True)

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

    @model_validator(mode="after")
    def _resolve_secrets(self) -> "Settings":
        """
        Secret hygiene:
        * JWT_SECRET_KEY falls back to SECRET_KEY (single canonical signing key).
        * Missing secrets get a random value in non-production so dev stays
          single-process-functional without a checked-in secret.
        * In production a missing key — or one of the old well-known dev
          placeholders — aborts startup instead of shipping with a guessable key.
        """
        old_dev_placeholder = "nexus-development-secret-key-min-32-chars-long-must-be-secure"
        old_dev_encryption = "nexus-aes256-key-32bytes-long!!"

        self.JWT_SECRET_KEY = self.JWT_SECRET_KEY or self.SECRET_KEY

        if self.ENVIRONMENT == "production":
            missing: list[str] = []
            if not self.SECRET_KEY or self.SECRET_KEY == old_dev_placeholder:
                missing.append("SECRET_KEY")
            if not self.ENCRYPTION_KEY or self.ENCRYPTION_KEY == old_dev_encryption:
                missing.append("ENCRYPTION_KEY")
            if missing:
                raise ValueError(
                    "Refusing to start in production with missing/insecure secrets: "
                    f"{', '.join(missing)}. Set them explicitly in the environment."
                )
        else:
            if not self.SECRET_KEY or self.SECRET_KEY == old_dev_placeholder:
                self.SECRET_KEY = secrets.token_urlsafe(48)
                logger.info("generated_random_development_secret_key")
            if not self.JWT_SECRET_KEY:
                self.JWT_SECRET_KEY = self.SECRET_KEY
            if not self.ENCRYPTION_KEY or self.ENCRYPTION_KEY == old_dev_encryption:
                self.ENCRYPTION_KEY = secrets.token_hex(32)  # 64 hex chars → 32 bytes
                logger.info("generated_random_development_encryption_key")

        if not self.PROMPT_DIR:
            self.PROMPT_DIR = str(Path(__file__).resolve().parent.parent / "prompt_templates")
        return self


settings = Settings()
