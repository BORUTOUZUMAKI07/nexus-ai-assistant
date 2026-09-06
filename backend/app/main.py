"""
Main FastAPI Application Entrypoint for Nexus AI Assistant.
Configures Lifespan, CORS, Middleware, FastMCP SSE Mount, and API v1 Routes.
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from backend.app.agents.orchestrator.graph import lifespan_graph
from backend.app.api.v1.api import api_router
from backend.app.core.config import settings
from backend.app.core.exceptions import NexusException
from backend.app.core.logging import get_logger, setup_logging
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.infrastructure.database.engine import close_db, init_db
from backend.app.infrastructure.vector.qdrant_client import vector_db
from backend.app.mcp.server import mcp
from fastapi import FastAPI, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

# 1. Initialize Structured Logging
setup_logging()
logger = get_logger("nexus.main")

# 2. Initialize Sentry if DSN is configured
if settings.SENTRY_DSN:
    import sentry_sdk
    sentry_sdk.init(
        dsn=settings.SENTRY_DSN,
        environment=settings.ENVIRONMENT,
        traces_sample_rate=1.0,
    )
    logger.info("sentry_initialized", env=settings.ENVIRONMENT)



@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager. Handles startup initialization
    and graceful shutdown of connections and pools.
    """
    logger.info("nexus_ai_starting_up", version="1.0.0", env=settings.ENVIRONMENT)

    # 1. Initialize Database Tables
    try:
        await init_db()
        logger.info("database_tables_initialized")
    except Exception as exc:
        logger.warning("database_init_failed_or_offline", error=str(exc))

    # 2. Ping Redis
    try:
        pong = await redis_client.ping()
        logger.info("redis_connection_verified", pong=pong)
    except Exception as exc:
        logger.warning("redis_offline_continuing_in_degraded_mode", error=str(exc))

    # 3. Ensure Qdrant Vector Collection exists
    try:
        await vector_db.ensure_collection()
        logger.info("qdrant_collection_verified")
    except Exception as exc:
        logger.warning("qdrant_init_failed_or_offline", error=str(exc))

    # 4. Open LangGraph AsyncPostgresSaver connection pool (durable short-term memory)
    async with lifespan_graph():
        logger.info("langgraph_postgres_checkpointer_ready")

        yield  # ← App serves requests here

    # Graceful Shutdown (outside lifespan_graph context — pool already closed)
    logger.info("nexus_ai_shutting_down")
    await close_db()
    await redis_client.close()


app = FastAPI(
    title=settings.PROJECT_NAME,
    description="Production Domain-Driven AI Assistant Backend (Free-tier compatible)",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# 1. CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# 2. Global Exception Handlers (RFC-7807 Problem Details)
@app.exception_handler(NexusException)
async def nexus_exception_handler(request: Request, exc: NexusException):
    """Handles all domain-specific Nexus exceptions with structured logging."""
    logger.warning(
        "nexus_domain_exception",
        path=request.url.path,
        status=exc.status_code,
        error_code=exc.error_code,
        detail=exc.message,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catches any unhandled unexpected runtime crashes to prevent raw stack traces leaking."""
    logger.error(
        "unhandled_internal_server_error",
        path=request.url.path,
        error=str(exc),
        exc_info=True,
    )
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "type": "https://nexus-assistant.ai/errors/internal-server-error",
            "title": "INTERNAL_SERVER_ERROR",
            "status": 500,
            "detail": "An unexpected internal server error occurred. Please contact support.",
        },
    )


# 3. Health check endpoints
@app.get("/health", tags=["system"])
async def health_check():
    return {
        "status": "healthy",
        "service": "Nexus AI Assistant",
        "environment": settings.ENVIRONMENT,
        "default_model": settings.DEFAULT_MODEL,
        "free_tier_ready": True,
    }


@app.get("/", tags=["system"])
async def root():
    return {
        "message": "Welcome to Nexus AI Assistant API",
        "docs": "/docs",
        "version": "1.0.0",
    }


# 4. Mount API v1 Routes
app.include_router(api_router, prefix=settings.API_V1_PREFIX)

# 5. Mount FastMCP HTTP/SSE App
try:
    if hasattr(mcp, "http_app"):
        mcp_subapp = mcp.http_app()
    elif hasattr(mcp, "sse_app"):
        mcp_subapp = mcp.sse_app()
    else:
        mcp_subapp = None

    if mcp_subapp:
        app.mount("/mcp", mcp_subapp)
        logger.info("fastmcp_mounted", path="/mcp")
except Exception as exc:
    logger.warning("fastmcp_mount_failed", error=str(exc))
