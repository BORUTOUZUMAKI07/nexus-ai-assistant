"""
Main FastAPI Application Entrypoint for Nexus AI Assistant.
Configures Lifespan, CORS, Middleware, FastMCP SSE Mount, and API v1 Routes.
"""
import asyncio
import os
import sys
import time
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any

# psycopg (used by the LangGraph AsyncPostgresSaver checkpointer) cannot run in
# async mode on Windows' ProactorEventLoop. uvicorn resolves its event-loop
# factory BEFORE importing this module and unconditionally picks Proactor on
# Windows, so for the real server the loop must be supplied via uvicorn's
# `--loop backend.app.infrastructure.common.event_loop:event_loop_factory` flag
# (see start-backend.cmd). The policy here only covers in-process runners
# (TestClient, scripts, pytest) that call asyncio.run() themselves.
if sys.platform == "win32" and hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from backend.app.agents.orchestrator.graph import lifespan_graph
from backend.app.api.v1.api import api_router
from backend.app.api.v1.metrics import router as metrics_router
from backend.app.core.config import settings
from backend.app.core.db_target import describe_database_target
from backend.app.core.exceptions import NexusException
from backend.app.core.logging import get_logger, setup_logging
from backend.app.infrastructure.cache.redis_client import redis_client
from backend.app.infrastructure.database.engine import check_database_health, close_db, init_db
from backend.app.infrastructure.resilience.rate_limit import (
    RateLimitError,
)
from backend.app.infrastructure.vector.qdrant_client import vector_db
from backend.app.mcp.server import mcp
from backend.app.services.observability.metrics import metrics_collector
from backend.app.services.observability.otel import get_tracer, shutdown_otel
from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

# 1. Initialize Structured Logging
setup_logging()
logger = get_logger("nexus.main")


# 2. Build the FastMCP sub-application before the parent app exists.
#
# `mcp.http_app()` returns a `StarletteWithLifespan`: a Starlette app whose own
# `lifespan` starts (and stops) the streamable-HTTP session-manager task group.
# Mounting that sub-app does NOT start it -- the parent app has to enter
# `mcp_subapp.lifespan(...)`, which happens in `lifespan()` below. Without that
# entry every `/mcp` request answers 500 ("task group is not initialized") while
# the mount looks perfectly healthy (finding #14). Building the sub-app here,
# rather than after `app = FastAPI(...)`, is what lets the parent lifespan
# reference it; `mcp_subapp = None` up front means a failed build cannot leave
# the name unbound.
mcp_subapp: Any = None


async def _mcp_token_is_valid(token: str) -> bool:
    """True only if the REST dependency chain would accept this access token.

    The MCP transport is a second front door onto tool execution, so it must
    apply the *same* rules as `get_current_user`: a structurally valid token is
    not enough -- the `jti` must not be revoked on logout, the `sub` must name a
    real user row, and that row must still be active. Delegating to
    `deps.resolve_access_token_user` (rather than a local re-implementation) is
    what stops the two surfaces from drifting apart (finding #11).

    A Redis outage is fail-open, exactly as on the REST path. A database outage
    denies the request, because MCP tools need the database anyway.
    """
    from backend.app.api.deps import resolve_access_token_user
    from backend.app.infrastructure.database.session import async_session_factory

    async with async_session_factory() as session:
        user = await resolve_access_token_user(token, session)
    return user is not None


async def _mcp_request_is_authorized(request: Request) -> bool:
    """Gate every MCP request on a valid Bearer JWT, cookie, or shared key."""
    if not settings.MCP_AUTH_ENABLED:
        return True
    try:
        authz = request.headers.get("Authorization", "")
        if authz.lower().startswith("bearer "):
            return await _mcp_token_is_valid(authz[7:].strip())
        cookie_token = request.cookies.get("nexus_access_token")
        if cookie_token:
            return await _mcp_token_is_valid(cookie_token)
        mcp_key = request.headers.get("x-nexus-mcp-key")
        if mcp_key and settings.MCP_API_KEY and mcp_key == settings.MCP_API_KEY:
            return True
    except Exception as exc:
        logger.warning("mcp_auth_check_error", error_type=type(exc).__name__)
    return False


class _MCPAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if not await _mcp_request_is_authorized(request):
            return JSONResponse(
                status_code=status.HTTP_401_UNAUTHORIZED,
                content={"detail": "MCP endpoint requires authentication"},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)


# The MCP endpoint exposes agent tool execution (web search, code execution), so
# it must never listen anonymously; the middleware above authenticates every
# request before it reaches a tool.
try:
    if hasattr(mcp, "http_app"):
        mcp_subapp = mcp.http_app()
    elif hasattr(mcp, "sse_app"):
        mcp_subapp = mcp.sse_app()
    if mcp_subapp is not None:
        mcp_subapp.add_middleware(_MCPAuthMiddleware)
except Exception as exc:
    logger.warning("fastmcp_subapp_build_failed", error_type=type(exc).__name__)
    mcp_subapp = None


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Application lifespan manager. Handles startup initialization
    and graceful shutdown of connections and pools.
    """
    logger.info("nexus_ai_starting_up", version="1.0.0", env=settings.ENVIRONMENT)

    # 0. Log the redirect_uri each configured provider will actually send. A
    #    mismatch is answered by the provider straight to the browser (Google's
    #    "Error 400: redirect_uri_mismatch"), before this service sees anything,
    #    so this line is the only evidence the deployment can produce.
    try:
        from backend.app.services.auth.oauth import log_oauth_redirect_uris

        log_oauth_redirect_uris()
    except Exception as exc:
        logger.warning("oauth_redirect_uri_audit_failed", error_type=type(exc).__name__)

    # 0b. Report configured-but-unusable provider credentials. Previously a
    #    missing or placeholder key was only discovered mid-request, where the
    #    symptom was a silent capability downgrade and the only clue was a
    #    print() in an exception handler. Never raises: one broken provider must
    #    not stop the process from serving every other capability. Findings
    #    carry the variable name and a reason, never any secret material.
    try:
        from backend.app.core.credential_check import (
            format_report,
            validate_settings,
        )

        _cred_report = validate_settings(settings)
        if _cred_report.errors:
            logger.error("credential_validation_failed", **_cred_report.errors[0].as_log_kwargs())
            for _line in format_report(_cred_report):
                logger.warning("credential_validation_detail", detail=_line)
        elif _cred_report.warnings:
            for _line in format_report(_cred_report):
                logger.warning("credential_validation_detail", detail=_line)
        else:
            logger.info("credential_validation_ok", checked=_cred_report.checked)
    except Exception as exc:
        logger.warning("credential_validation_skipped", error_type=type(exc).__name__)

    # 1. Initialize Database Tables
    #
    # The target is logged BEFORE init_db(), never after: `init_db` runs
    # `create_all`, which issues CREATE TABLE for anything missing, so this is a
    # schema write against whatever DATABASE_URL points at. On 2026-10-01
    # `backend/.env` was found carrying a hosted Supabase DSN, which made the
    # production database the default target for every local run -- silently,
    # because nothing in the boot path looked. This line is unconditional: the
    # local case needs it as much as the remote one, since "it said localhost"
    # is the only reason to trust the next command.
    #
    # Deliberately a warning and not a refusal. Booting against a remote
    # database is legitimate -- that is how the deployed app runs -- and
    # blocking it would break production while protecting nothing. The hard gate
    # belongs on the command that changes schema, and lives in
    # `migrations/env.py::assert_migration_target_allowed`.
    _db_target = describe_database_target(settings.DATABASE_URL)
    logger.info("database_target_resolved", **_db_target.as_log_kwargs())
    if not _db_target.is_local:
        logger.warning(
            "database_target_is_not_local",
            detail=(
                f"Using the REMOTE database {_db_target.describe()}. Writes, "
                f"including the create_all in init_db(), go to it. If you meant "
                f"to work locally, set DATABASE_URL to "
                f"postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev "
                f"(matches docker-compose) or remove DATABASE_URL from "
                f"backend/.env to fall back to that default."
            ),
            **_db_target.as_log_kwargs(),
        )

    # Share links, password-reset/verification e-mails, OAuth redirect fallbacks
    # and organization invite URLs are all built from APP_PUBLIC_URL. A
    # localhost value in production does not fail loudly: the link is generated,
    # e-mailed, and then points at a URL that only exists on some developer's
    # machine -- the same silent-failure shape as DATABASE_URL above, which is
    # why this is checked at boot rather than left to be discovered in an inbox.
    _public_url = (settings.APP_PUBLIC_URL or "").strip().lower()
    if settings.ENVIRONMENT == "production" and _public_url.startswith(
        ("http://localhost", "http://127.0.0.1", "https://localhost", "https://127.0.0.1")
    ):
        logger.warning(
            "app_public_url_is_localhost",
            app_public_url=settings.APP_PUBLIC_URL,
            detail=(
                "Every share link, invite link and e-mail link built this run "
                "will point at localhost. Set APP_PUBLIC_URL to the public "
                "frontend origin (the Vercel URL) and redeploy."
            ),
        )

    try:
        await init_db()
        logger.info("database_tables_initialized")
    except Exception as exc:
        logger.warning("database_init_failed_or_offline", error_type=type(exc).__name__)

    # 2. Ping Redis
    try:
        pong = await redis_client.ping()
        logger.info("redis_connection_verified", pong=pong)
    except Exception as exc:
        logger.warning("redis_offline_continuing_in_degraded_mode", error_type=type(exc).__name__)

    # 3. Ensure Qdrant Vector Collection exists
    try:
        await vector_db.ensure_collection()
        logger.info("qdrant_collection_verified")
    except Exception as exc:
        logger.warning("qdrant_init_failed_or_offline", error_type=type(exc).__name__)

    # 4. Open LangGraph AsyncPostgresSaver connection pool (durable short-term memory)
    async with lifespan_graph():
        # 4b. Load lifecycle hook policies into the gateway registry (fail-open:
        #     an empty registry simply means no hooks are active).
        try:
            from backend.app.infrastructure.database.session import async_session_factory
            from backend.app.services.hooks.service import HookService
            async with async_session_factory() as session:
                await HookService(session).reload_registry()
            logger.info("hook_registry_loaded_at_startup")
        except Exception as exc:
            logger.warning("hook_registry_startup_load_failed", error=str(exc))

        logger.info("langgraph_postgres_checkpointer_ready")

        # 4c. Start the FastMCP sub-application's own lifespan so its
        #     streamable-HTTP session-manager task group is running before the
        #     first request. Mounting the sub-app does not start it; without this
        #     entry every /mcp request answers 500 (finding #14). Entered inside
        #     this block (and before `yield`) so shutdown unwinds it in order.
        _mcp_lifespan = getattr(mcp_subapp, "lifespan", None)
        if _mcp_lifespan is None:
            if mcp_subapp is not None:
                # A mounted transport with no lifespan would answer 500 on every
                # request; surface that at boot rather than silently.
                logger.error(
                    "mcp_transport_lifespan_missing",
                    detail="FastMCP sub-app has no lifespan; /mcp requests will fail",
                )
            yield
        else:
            try:
                if app is not None:
                    lifespan_cm = _mcp_lifespan(app)
                else:
                    # When driven without an app (e.g. some tests), try the
                    # lifespan without arguments; fall back to best-effort.
                    try:
                        lifespan_cm = _mcp_lifespan()
                    except TypeError:
                        lifespan_cm = _mcp_lifespan(app)
                async with lifespan_cm:
                    if app is not None and hasattr(app, "state"):
                        try:
                            app.state.mcp_transport_started = True
                        except Exception as exc:
                            # `app.state` is a sealed object in some test
                            # harnesses; the flag is only a readiness hint, so
                            # failing to set it must not stop the transport.
                            logger.debug(
                                "mcp_transport_state_flag_not_set",
                                value=True,
                                error_type=type(exc).__name__,
                            )
                    try:
                        yield
                    finally:
                        if app is not None and hasattr(app, "state"):
                            try:
                                app.state.mcp_transport_started = False
                            except Exception as exc:
                                logger.debug(
                                    "mcp_transport_state_flag_not_set",
                                    value=False,
                                    error_type=type(exc).__name__,
                                )
            except Exception:
                # Never let MCP lifespan break core startup.
                yield

    # Graceful Shutdown (outside lifespan_graph context — pool already closed)
    logger.info("nexus_ai_shutting_down")
    # Layer-1: flush + detach the OTLP pipelines so the collector receives the
    # final log lines and the last batch of spans/metrics (fail-open — teardown
    # must never break shutdown).
    try:
        from backend.app.services.observability.logs import shutdown_otlp_log_handler

        shutdown_otlp_log_handler()
    except Exception as exc:  # noqa: BLE001 - fail-open seam
        logger.warning("otlp_log_shutdown_failed", error_type=type(exc).__name__)
    try:
        shutdown_otel()
    except Exception as exc:  # noqa: BLE001 - fail-open seam
        logger.warning("otlp_signal_shutdown_failed", error_type=type(exc).__name__)
    await close_db()
    if hasattr(redis_client, "aclose"):
        await redis_client.aclose()
    else:
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


# 1b. OTLP HTTP server spans + request metrics. Spans are a no-op unless the
#     OTLP pipeline is wired (NEW_RELIC_ENABLED + a collector endpoint); when it
#     is, every request is a span, and any span opened inside a request (the node
#     spans in services/observability/tracing.py) nests under it. Request
#     latency/throughput/error samples always feed the in-process
#     MetricsCollector — the `/metrics` debug surface — and that collector
#     forwards them to OTel instruments once the meter provider is wired. This is
#     the infra/ops counterpart to LangSmith's LLM/agent trace view.
def _record_http_request_metrics(status_code: int, duration_ms: float) -> None:
    metrics_collector.record_latency("http_request", duration_ms)
    metrics_collector.increment("http_requests_total")
    if status_code >= 500:
        metrics_collector.record_error(f"http_{status_code}")


@app.middleware("http")
async def otlp_http_span_middleware(request: Request, call_next):
    start = time.perf_counter()
    tracer = get_tracer("backend.app.http")
    if tracer is None:
        response = await call_next(request)
        _record_http_request_metrics(
            response.status_code, (time.perf_counter() - start) * 1000.0
        )
        return response

    from opentelemetry.trace import Status, StatusCode

    with tracer.start_as_current_span(f"{request.method} {request.url.path}") as span:
        span.set_attribute("http.request.method", request.method)
        span.set_attribute("url.path", request.url.path)
        if request.url.query:
            span.set_attribute("url.query", request.url.query)
        host = request.headers.get("host")
        if host:
            span.set_attribute("server.address", host)
        try:
            response = await call_next(request)
        except Exception as exc:
            span.record_exception(exc)
            span.set_status(Status(StatusCode.ERROR, type(exc).__name__))
            span.set_attribute("http.response.status_code", 500)
            _record_http_request_metrics(500, (time.perf_counter() - start) * 1000.0)
            raise
        span.set_attribute("http.response.status_code", response.status_code)
        if response.status_code >= 500:
            span.set_status(Status(StatusCode.ERROR, str(response.status_code)))
        _record_http_request_metrics(
            response.status_code, (time.perf_counter() - start) * 1000.0
        )
        return response


# 2. Global Exception Handlers (RFC-7807 Problem Details)
@app.exception_handler(RateLimitError)
async def rate_limit_exceeded_handler(request: Request, exc: RateLimitError):
    """429 with Retry-After (RFC 6585) for the rate-limit dependency.

    Kept separate from NexusException so the standard rate-limit headers are
    always applied regardless of the error's origin. The exception carries the
    full ``X-RateLimit-*`` payload (headers set on the injected response are
    discarded when an exception handler returns a fresh response).
    """
    return JSONResponse(
        status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        content={
            "type": "https://nexus-assistant.ai/errors/rate-limit-exceeded",
            "title": "RATE_LIMIT_EXCEEDED",
            "status": status.HTTP_429_TOO_MANY_REQUESTS,
            "detail": str(exc),
        },
        headers=exc.headers,
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    """HTTPException responses that originate on a rate-limited route keep the
    standard ``X-RateLimit-*`` headers.

    The rate-limit dependency writes its headers onto both the injected
    ``Response`` and ``request.state.rate_limit_headers``; when an endpoint
    raises (e.g. login's 401), the injected response is discarded, so this
    handler re-emits the stashed payload while preserving ``exc.headers``
    (e.g. ``WWW-Authenticate``) and Starlette's ``{"detail": ...}`` shape.
    """
    headers = dict(getattr(exc, "headers", None) or {})
    headers.update(getattr(request.state, "rate_limit_headers", {}) or {})
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
        headers=headers or None,
    )


@app.exception_handler(NexusException)
async def nexus_exception_handler(request: Request, exc: NexusException):
    """Handles all domain-specific Nexus exceptions with structured logging."""
    logger.warning(
        "nexus_domain_exception",
        path=request.url.path,
        status=exc.status_code,
        error_code=exc.error_code,
        detail_length=len(exc.message) if isinstance(exc.message, str) else 0,
    )
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_dict(),
    )


# Postgres errors that mean "the schema on this database is older than the code".
#
# Production never runs `create_all` (init_db returns early when
# ENVIRONMENT=production), so the schema is whatever the last `alembic upgrade
# head` produced. When it is behind the models, every query touching the missing
# column fails -- and the generic handler below turned that into "An unexpected
# internal server error occurred": three endpoints down, the operator told to
# contact support, and no way to tell drift from a code bug without the logs.
_SCHEMA_ERROR_PATTERNS = (
    "does not exist",              # missing column / relation
    "UndefinedColumnError",
    "UndefinedTableError",
)


def describe_schema_drift(exc: BaseException) -> str | None:
    """Return the offending Postgres detail when ``exc`` is schema drift.

    Only the SQLAlchemy wrapper is unwrapped, and only one line of the driver
    message is returned: it names a column or relation, never a value, so it is
    safe to show and enough to act on. Anything else returns None, leaving
    unrelated 500s with their generic body.
    """
    try:
        from sqlalchemy.exc import ProgrammingError
    except Exception:  # pragma: no cover - SQLAlchemy is a hard dependency
        return None
    if not isinstance(exc, ProgrammingError):
        return None
    message = str(getattr(exc, "orig", None) or exc)
    if not any(pattern in message for pattern in _SCHEMA_ERROR_PATTERNS):
        return None
    return message.splitlines()[0].strip()[:300]


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    """Catches any unhandled unexpected runtime crashes to prevent raw stack traces leaking."""
    drift = describe_schema_drift(exc)
    if drift is not None:
        logger.error(
            "database_schema_out_of_date",
            path=request.url.path,
            detail=drift,
            exc_info=True,
        )
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={
                "type": "https://nexus-assistant.ai/errors/database-schema-out-of-date",
                "title": "DATABASE_SCHEMA_OUT_OF_DATE",
                "status": 503,
                "detail": (
                    "The database schema is behind this build of the application: "
                    f"{drift}. Run `alembic upgrade head` against this database."
                ),
            },
        )

    logger.error(
        "unhandled_internal_server_error",
        path=request.url.path,
        error_type=type(exc).__name__,
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
    """Liveness probe: confirms the ASGI process is responding."""
    # `schema_reconcile` reports what the start-up repair did, so an operator can
    # tell "the deploy never ran it" from "it ran and failed" without the logs.
    from app.infrastructure.database.schema_sync import RECONCILE_STATE

    # Imported under `backend.app.*`, the namespace the API and this process's
    # own routers use, NOT the short `app.*` alias. Those are two module objects
    # for one file: an `app.services.runs.executor` import here created a second
    # `PERSIST_STATE` that nothing ever writes, so the probe reported `not_run`
    # while runs were executing and their writes failing.
    from backend.app.services.runs.executor import PERSIST_STATE

    return {
        "status": "healthy",
        "service": "Nexus AI Assistant",
        "schema_reconcile": RECONCILE_STATE,
        # A reply that streams but never persists leaves the transcript empty on
        # reload and usage at zero — a symptom that reads as a UI defect. The
        # write is fail-open by design, so its outcome has to be observable here.
        "run_persist": PERSIST_STATE,
        "environment": settings.ENVIRONMENT,
        # Render sets this for the running build. Without it there is no way
        # to tell "the fix is not deployed" from "the fix did not work" when
        # the only channel available is this probe.
        "commit": (os.environ.get("RENDER_GIT_COMMIT") or "")[:7],
    }


@app.get("/health/ready", tags=["system"])
async def readiness_check():
    """Readiness probe: returns 503 when the primary database is unavailable."""
    from fastapi.responses import JSONResponse

    database_ready = await check_database_health()
    if not database_ready:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "not_ready", "database": "unavailable"},
        )
    # `mcp_transport` reports whether the FastMCP sub-app's lifespan actually
    # started (finding #14). It deliberately does NOT drive the status code: the
    # API serves REST traffic fine with the MCP transport down, and a monitor
    # alerts on the field. Values: started | not_started | disabled.
    mcp_transport = (
        "started"
        if getattr(app.state, "mcp_transport_started", False)
        else ("disabled" if mcp_subapp is None else "not_started")
    )
    # Rate limiting shares Redis with the cache. When Redis is unreachable the
    # limiter degrades to a per-process window rather than failing open — correct,
    # but invisible unless someone reads the logs, which on a hosted origin nobody
    # does. Surfacing it here makes "is brute-force protection actually shared on
    # this deployment?" a single curl. Like `mcp_transport`, it does NOT move the
    # status code: auth still works, the ceiling is just per-instance.
    try:
        await redis_client.ping()
        rate_limit_store = "redis"
    except Exception:
        rate_limit_store = "in_process"
    return {
        "status": "ready",
        "database": "available",
        "mcp_transport": mcp_transport,
        "rate_limit_store": rate_limit_store,
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
app.include_router(metrics_router)

# 5. Mount the FastMCP sub-application onto the parent app.
if mcp_subapp is not None:
    app.mount("/mcp", mcp_subapp)
    logger.info("fastmcp_mounted", path="/mcp", auth_enabled=settings.MCP_AUTH_ENABLED)
else:
    logger.info("fastmcp_not_mounted")
