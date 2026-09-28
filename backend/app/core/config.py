"""
Application Settings — loaded from environment / .env file.
All optional keys default to None (free-tier compatible).
"""
import logging
import secrets
from pathlib import Path
from typing import cast

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
    # The single source for the app's display name. A second APP_NAME field with
    # the identical default used to sit here as a bare "# alias"; nothing read
    # it, so the two could drift apart silently. Use PROJECT_NAME.
    PROJECT_NAME: str = Field(default="Nexus AI Assistant")

    # ── API Routing ────────────────────────────────────────────────────────────
    # One setting for the mount prefix. API_V1_STR was a duplicate of this with
    # the same default, used in exactly one place (a Location header). Changing
    # API_V1_PREFIX left that header pointing at the old prefix, so the two are
    # now merged — every caller reads API_V1_PREFIX.
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
    # When a refresh token is presented twice (replay), revoke *every* refresh
    # token for that user for the remainder of the refresh-token lifetime
    # instead of only refusing the replayed one. Turns a silent token theft
    # into a visible, recoverable event (the user must sign in again) and stops
    # the attacker from riding a parallel session. Set False to keep the
    # previous per-token-only behaviour.
    REFRESH_REVOKE_ON_REUSE: bool = Field(default=True)

    # ── OAuth / OIDC SSO (Authorization Code + PKCE) ─────────────────────────
    # Generic OpenID Connect client (Google, GitHub, Azure AD, Keycloak…).
    # Leave OAUTH_CLIENT_ID empty to disable SSO: /auth/oauth/* then return
    # 404 and the frontend hides the "Continue with SSO" button.
    OAUTH_CLIENT_ID: str | None = Field(default=None, description="OIDC client id")
    OAUTH_CLIENT_SECRET: str | None = Field(default=None, description="OIDC client secret")
    OAUTH_AUTHORIZE_URL: str | None = Field(default=None, description="OIDC /authorize endpoint")
    OAUTH_TOKEN_URL: str | None = Field(default=None, description="OIDC /token endpoint")
    OAUTH_USERINFO_URL: str | None = Field(default=None, description="OIDC userinfo endpoint")
    OAUTH_SCOPE: str = Field(default="openid profile email", description="OIDC scopes")
    # Public origin of THIS backend: authlib builds the registered
    # redirect_uri = {OAUTH_BACKEND_URL}/api/v1/auth/oauth/callback from it.
    OAUTH_BACKEND_URL: str = Field(
        default="http://localhost:8000",
        description="Backend public origin used to build the OAuth redirect_uri",
    )
    # Lifetime of the stored PKCE state codes (one-time use).
    OAUTH_STATE_TTL_SECONDS: int = Field(default=600, description="PKCE state/verifier lifetime")

    # ── CORS ───────────────────────────────────────────────────────────────────
    # Single source for the browser allow-list. A second ALLOWED_ORIGINS field
    # with identical defaults used to sit here; only CORS_ORIGINS was ever read,
    # so an operator who edited ALLOWED_ORIGINS saw no effect and had no warning.
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
    # Only the service-role (admin) key belongs on a server. Every Storage call
    # authenticates with it. SUPABASE_ANON_KEY is a publishable, client-side key
    # that was declared here and never read; leaving it in the server config
    # only invited someone to reach for it where it would not be appropriate.
    SUPABASE_SERVICE_ROLE_KEY: str | None = None

    # ── Redis / Celery ─────────────────────────────────────────────────────────
    # One URL for cache, broker and results. CELERY_BROKER_URL and
    # CELERY_RESULT_BACKEND were separate fields pointing at databases /1 and /2
    # while Celery actually used this field's /0 — so configuring them looked
    # like it worked and did nothing.
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

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
    # Declared for operators who set them expecting direct-provider routing.
    # Nothing in this codebase reads them: every model in the LiteLLM Router's
    # model_list is an OpenRouter deployment, and a bare OpenAI/Anthropic/
    # Together model id would not resolve to any registered model_group, so
    # routing one in would fail rather than switch provider. Kept as pass-through
    # env for litellm itself, which reads these from the process environment.
    OPENAI_API_KEY: str | None = None
    ANTHROPIC_API_KEY: str | None = None
    TOGETHER_API_KEY: str | None = None

    # Default model aliases used across the app.
    #
    # Both are logical model names, resolved to a Router model_group by
    # litellm_client.resolve_model_group — the provider prefix is stripped, so
    # "groq/llama-3.1-8b-instant" and "llama-3.1-8b-instant" both land in
    # fast_chat.
    #
    # FAST_MODEL backs the cheap calls (planner, Tree-of-Thought, the coder /
    # critic / researcher subagents, RAGAS judging). It was previously unused:
    # those six call sites each hardcoded the literal "llama-3.1-8b-instant", so
    # pointing FAST_MODEL at a different fast model changed nothing.
    DEFAULT_MODEL: str = Field(default="groq/llama-3.3-70b-versatile")
    FAST_MODEL: str = Field(default="groq/llama-3.1-8b-instant")

    # ── Embeddings ─────────────────────────────────────────────────────────────
    EMBEDDING_MODEL: str = Field(default="models/gemini-embedding-001")
    EMBEDDING_DIMENSION: int = Field(default=768)
    # FlashRank cross-encoder used to re-order retrieved chunks. Must be one of
    # the names FlashRank ships (ms-marco-TinyBERT-L-2-v2, ms-marco-MiniLM-L-12-v2,
    # ms-marco-MultiBERT-L-12) — NOT a HuggingFace cross-encoder path.
    #
    # This used to default to "cross-encoder/ms-marco-MiniLM-L-6-v2", a
    # HuggingFace repo id that FlashRank cannot load. Nothing read the setting,
    # so the mismatch never mattered and the bad value went unnoticed; the
    # reranker silently kept using its own hardcoded ms-marco-TinyBERT-L-2-v2.
    # Wiring it up as-is would have made every rerank fail to load and quietly
    # fall back to plain vector-score ordering. Validated in RerankingService.
    RERANKER_MODEL: str = Field(default="ms-marco-TinyBERT-L-2-v2")

    # ── External Services ──────────────────────────────────────────────────────
    E2B_API_KEY: str | None = None
    TAVILY_API_KEY: str | None = None
    FIRECRAWL_API_KEY: str | None = None
    HELICONE_API_KEY: str | None = None
    MEM0_API_KEY: str | None = None
    MEMORY_EXTRACTION_MODEL: str = Field(default="groq/llama-3.1-8b-instant")

    # ── Object Storage (Supabase Storage — uses SUPABASE_URL + SERVICE_ROLE_KEY) ─
    # Storage goes through the Supabase REST API (infrastructure/storage/
    # supabase_storage.py), which needs no boto2 credentials. The four
    # SUPABASE_S3_* fields that used to sit here were the boto3-era settings from
    # the old app/settings.py, read by nothing once the REST client landed.
    #
    # Two of them were duplicates of live settings, which is why they were worth
    # removing rather than keeping: SUPABASE_S3_BUCKET repeated STORAGE_BUCKET
    # and STORAGE_MAX_FILE_SIZE_MB repeated MAX_UPLOAD_SIZE_MB, both with
    # identical defaults. Editing either one did nothing.
    #
    # The upload ceiling is MAX_UPLOAD_SIZE_MB (enforced in api/v1/files.py).
    STORAGE_BUCKET: str = Field(default="nexus-knowledge", description="Supabase Storage bucket name")

    # ── Prompt templates ──────────────────────────────────────────────────────
    # Single source of truth, computed at import so the default works in any
    # checkout without duplication.
    PROMPT_DIR: str = Field(
        default="",
        description="Directory containing prompt template .txt files",
    )

    # ── Observability (LangSmith & Sentry Free Tiers) ──────────────────────────
    # Read from the process environment by the LangSmith SDK itself, not by this
    # codebase — core.config calls load_dotenv() at import, so the variables are
    # present in os.environ. Declared here so they are documented, defaulted and
    # type-checked alongside everything else; do not delete them from .env on the
    # grounds that nothing references these attributes.
    LANGSMITH_API_KEY: str | None = None
    LANGSMITH_PROJECT: str = Field(default="nexus-ai-assistant")
    LANGSMITH_TRACING: bool = Field(default=False)
    SENTRY_DSN: str | None = None
    # Verbosity for the whole app. None means "work it out from ENVIRONMENT":
    # INFO in production, DEBUG elsewhere. Set it to override that explicitly
    # (e.g. LOG_LEVEL=DEBUG on a production box to debug one incident).
    #
    # This used to default to the string "INFO" and was never read — logging
    # picked its level straight from settings.ENVIRONMENT — so setting it did
    # nothing. Resolution and validation live in core/logging.setup_logging.
    LOG_LEVEL: str | None = None

    # ── Rate Limiting ──────────────────────────────────────────────────────────
    # Size of the sliding-window token bucket, per (scope, tenant), enforced in
    # infrastructure/resilience/rate_limit.py via a Redis Lua script.
    #
    # Note it fails OPEN: if Redis is unreachable the limiter allows the request
    # rather than rejecting it, so this is a cost control, not an availability
    # control, and it will not protect the backend from a flood during a cache
    # outage. The outage is counted as nexus_requests_total{operation=
    # "redis_fail_open"} for that reason.
    RATE_LIMIT_PER_MINUTE: int = Field(default=100)
    #
    # A companion cap on *spend* per streaming response used to sit here as
    # RATE_LIMIT_STREAM_COST. It was removed rather than wired: spend-based
    # limiting has never been implemented anywhere in this codebase — there is
    # no per-response cost accumulator, only the per-minute request counter
    # above — so it was a setting for a feature that did not exist.
    #
    # Reinstating it means a real feature, not a wire: a Redis float counter
    # keyed per tenant, incremented as usage is recorded and checked alongside
    # the request limit. It would also need its own window setting, since this
    # one was per-minute-denominated and cost is not. It is intentionally not
    # added back on a speculative basis, to keep the invariant that every
    # field in this class is actually read by the application.

    # ── PII Redaction ──────────────────────────────────────────────────────────
    # When True, a structlog processor scrubs emails, phone numbers, SSNs,
    # credit cards, IPs and bearer/secret tokens from every emitted log event
    # (industry-grade log hygiene — MD §8.6 security/privacy checklist).
    PII_REDACTION_ENABLED: bool = Field(default=False)
    PII_REDACTION_REPLACEMENT: str = Field(default="[REDACTED]")

    # ── Observability: Langfuse (optional, env-gated) ─────────────────────────
    # Wires the LiteLLM success/failure callbacks into Langfuse when enabled so
    # every completion/stream is traceable with token+cost accounting (the #1
    # industry expectation for agent products). No-op when disabled or when the
    # langfuse package is not installed.
    LANGFUSE_ENABLED: bool = Field(default=False)
    LANGFUSE_HOST: str | None = Field(default=None, description="Langfuse base URL (defaults to https://cloud.langfuse.com)")
    LANGFUSE_PUBLIC_KEY: str | None = Field(default=None)
    LANGFUSE_SECRET_KEY: str | None = Field(default=None)

    # ── Response Caching (semantic-cost redaction, MD §8.5) ───────────────────
    # Exact-normalized-query response cache for the synchronous message path.
    # Keyed per user+model; stores the generated text + token/cost telemetry so
    # repeated identical questions skip the LLM round-trip. Fail-open.
    RESPONSE_CACHE_ENABLED: bool = Field(default=False)
    RESPONSE_CACHE_TTL_SECONDS: int = Field(default=3600)
    RESPONSE_CACHE_MIN_LENGTH: int = Field(default=8, description="Minimum query length eligible for caching")

    # ── Experiments / Canary-Shadow (MD §6.6 safe release) ────────────────────
    # Deterministic user-bucket assignment for shadow/gradual prompt release.
    # EXPERIMENTS_CONFIG_PATH may point at a YAML file describing experiments
    # and variant weights; when unset, experiments resolve to their default
    # variant (zero behavioral change).
    EXPERIMENTS_CONFIG_PATH: str | None = Field(default=None)
    EXPERIMENTS_DEFAULT_BUCKETS: int = Field(default=100)

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

    # ── Candidate feature sweep (CRAG / confidence / bandit / optimizer) ──────
    # Knobs for the industry-aligned features landed in the "8 candidates"
    # milestone. Each is fail-open: wrong tuning degrades gracefully, never
    # crashes the hot path.
    CRAG_MAX_REVISIONS: int = Field(
        default=1,
        description="CRAG corrective retrieval: max refined local re-queries before falling back to web search",
    )
    CONFIDENCE_THRESHOLD: float = Field(
        default=0.6,
        description="Calibrated confidence gate: composite confidence below this marks a response low-confidence",
    )
    BANDIT_EPSILON: float = Field(
        default=0.1,
        description="ε-greedy exploration rate for bandit-selected experiment variants",
    )
    BANDIT_REWARD_WINDOW: int = Field(
        default=200,
        description="Most-recent reward rows considered when recomputing empirical arm means",
    )
    OPTIMIZATION_DEFAULT_CANDIDATES: int = Field(
        default=3,
        description="Default candidate rewrites proposed per prompt-optimization run",
    )

    # ── HITL Approvals ────────────────────────────────────────────────────────
    HITL_APPROVAL_TIMEOUT_SECONDS: int = Field(default=900, description="How long a parked HITL approval stays valid before auto-expiring")

    # ── Two-Factor Authentication (TOTP, RFC 6238) ────────────────────────────
    TOTP_ISSUER: str = Field(default="Nexus AI Assistant")
    TOTP_VALID_WINDOW: int = Field(default=1, description="±1 step tolerance for clock drift")
    TOTP_PREAUTH_MINUTES: int = Field(default=5, description="Lifetime of the 2FA challenge (preauth) token")

    # ── Email verification / password reset ───────────────────────────────────
    EMAIL_VERIFICATION_REQUIRED: bool = Field(default=False, description="When True, new users cannot authenticate until email is verified (off preserves the current free-tier flow)")
    EMAIL_VERIFY_TOKEN_MINUTES: int = Field(default=1440, description="24h default for verification links")
    PASSWORD_RESET_TOKEN_MINUTES: int = Field(default=30)
    APP_PUBLIC_URL: str = Field(default="http://localhost:3000", description="Public origin used to build magic links")

    # ── Outbound webhooks ─────────────────────────────────────────────────────
    WEBHOOK_MAX_ATTEMPTS: int = Field(default=3, description="Delivery attempts before a webhook delivery is terminal")

    # ── Read-only conversation shares ─────────────────────────────────────────
    SHARE_DEFAULT_TTL_SECONDS: int | None = Field(default=None, description="Optional global expiry for new share links (None = no expiry)")

    # ── Text-to-speech (TTS) ──────────────────────────────────────────────────
    # Key-less Microsoft Edge neural voices (edge-tts) — free, no API key,
    # no billing. TTS_VOICE is an edge-tts voice id (e.g. en-US-AriaNeural).
    # /audio/speech always works; no provider key is ever required.
    # Validated on every synthesis call by litellm_client.tts_media_type(),
    # which rejects anything outside these sets instead of ignoring it.
    TTS_MODEL: str = Field(default="edge-tts")
    TTS_VOICE: str = Field(default="en-US-AriaNeural")
    TTS_FORMAT: str = Field(default="mp3")

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

    # ── Observability / metrics ─────────────────────────────────────────────────
    # Bearer token guarding the Prometheus /metrics scrape endpoint. When set,
    # scrapers must send `Authorization: Bearer <METRICS_TOKEN>`. When unset the
    # endpoint is open (local/dev scraping) — always set it in production.
    METRICS_TOKEN: str | None = Field(default=None)

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def parse_cors(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            import json
            try:
                return cast("list[str]", json.loads(v))
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
