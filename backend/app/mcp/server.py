"""
FastMCP Server Implementation for Nexus AI.
Exposes Tools, Resources, and Prompts following official Model Context Protocol (MCP) standards.
Can run standalone or mount into FastAPI via SSE transport.
"""
try:
    from fastmcp import FastMCP
except ImportError:
    try:
        from mcp.server.mcpserver import MCPServer as FastMCP
    except ImportError:
        try:
            from mcp.server.fastmcp import FastMCP
        except (ImportError, ModuleNotFoundError):
            class FastMCP:
                def __init__(self, name: str, **kwargs):
                    self.name = name
                    self.version = kwargs.get("version")
                def tool(self):
                    def decorator(fn): return fn
                    return decorator
                def resource(self, uri: str):
                    def decorator(fn): return fn
                    return decorator
                def prompt(self):
                    def decorator(fn): return fn
                    return decorator
                def sse_app(self):
                    from fastapi import FastAPI
                    return FastAPI()

import ast
import json
import math
import operator

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

# Initialize FastMCP Server.
#
# Identity comes from settings so the name MCP clients see in the initialize
# handshake is configurable. It used to be the literal "Nexus-MCP-Server", which
# contradicted the "nexus-mcp" this same file reports from its server-info
# resource and the MCP_SERVER_NAME=nexus-mcp line in .env.example — three names
# for one server, none of them connected to configuration.
#
# `version` is passed only where the installed FastMCP accepts it. The fallback
# class above and the two legacy SDK import paths take a name alone, so a
# TypeError here degrades to the older call rather than breaking startup.
_MCP_IDENTITY = {"name": settings.MCP_SERVER_NAME, "version": settings.MCP_SERVER_VERSION}
try:
    mcp = FastMCP(**_MCP_IDENTITY)
except TypeError:
    logger.info("mcp_version_unsupported_falling_back_to_name_only", **_MCP_IDENTITY)
    mcp = FastMCP(_MCP_IDENTITY["name"])

_ALLOWED_MATH_NAMES = {n: getattr(math, n) for n in dir(math) if not n.startswith("_")}


# ==========================================
# 1. FastMCP Tools
# ==========================================
@mcp.tool()
async def web_search(query: str, max_results: int = 5) -> str:
    """
    Search the web for up-to-date information, news, or articles.
    """
    from backend.app.services.tools.web_search import web_search_service

    results = await web_search_service.search(query=query, max_results=max_results)
    if not results:
        return f"No results found for query: '{query}'"
    return "\n\n".join([f"**{r['title']}**\n{r['snippet']}\n{r['url']}" for r in results])


@mcp.tool()
async def execute_python_code(code: str, timeout_seconds: int = 30) -> str:
    """
    Execute Python code in an isolated E2B microVM sandbox and capture stdout/stderr.
    """
    from backend.app.services.tools.code_execution import code_executor

    res = await code_executor.execute_python(code=code, timeout_seconds=timeout_seconds)
    output = []
    if res.get("stdout"):
        output.append(f"Stdout:\n{res['stdout']}")
    if res.get("stderr"):
        output.append(f"Stderr:\n{res['stderr']}")
    if res.get("error"):
        output.append(f"Error:\n{res['error']}")
    return "\n".join(output) if output else "Code executed with no output."


_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}

# CPU/memory DoS guard for the AST evaluator: `10**999999999` or
# `math.factorial(10**8)` would otherwise allocate a multi-gigabyte int.
_MAX_EXPONENT = 1000
_MAX_RESULT_MAGNITUDE = 10**1000  # ~1001 digits


def _check_result(value: float | int) -> float | int:
    """Reject non-finite or absurdly large computation results."""
    if isinstance(value, float):
        import math as _math

        if _math.isinf(value) or _math.isnan(value):
            raise ValueError("Result is not a finite number")
    if isinstance(value, (int, float)) and abs(value) > _MAX_RESULT_MAGNITUDE:
        raise ValueError("Result out of safe range")
    return value


def _safe_math_eval(expression: str) -> float | int:
    """
    Restricted AST interpreter for arithmetic expressions. Unlike eval(), this
    whitelists the parse tree node-by-node: only numeric literals, the four
    operators, unary signs, and known ``math`` names/functions are accepted.
    Attribute access, subscripts, comprehensions, imports and all dunder names
    are structurally impossible, so no sandbox escape vector exists.
    """
    if not expression or not expression.strip():
        raise ValueError("Empty expression")
    if len(expression) > 500:
        raise ValueError("Expression is too long (maximum 500 characters)")

    tree = ast.parse(expression, mode="eval")
    if sum(1 for _ in ast.walk(tree)) > 100:
        raise ValueError("Expression is too complex (maximum 100 syntax nodes)")

    def _eval(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval(node.body)
        if isinstance(node, ast.Constant):
            if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
                return node.value
            raise ValueError("Only numeric constants are allowed")
        if isinstance(node, ast.BinOp):
            op_fn = _BIN_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            left = _eval(node.left)
            right = _eval(node.right)
            if isinstance(node.op, ast.Pow):
                if abs(right) > _MAX_EXPONENT:
                    raise ValueError(f"Exponent too large (max {_MAX_EXPONENT})")
                if abs(left) > 1e100:
                    raise ValueError("Base magnitude exceeds the supported limit")
                return _check_result(left**right)
            return _check_result(op_fn(left, right))
        if isinstance(node, ast.UnaryOp):
            op_fn = _UNARY_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return _check_result(op_fn(_eval(node.operand)))
        if isinstance(node, ast.Name):
            if node.id in _ALLOWED_MATH_NAMES:
                return _ALLOWED_MATH_NAMES[node.id]
            raise ValueError(f"Unknown symbol: {node.id}")
        if isinstance(node, ast.Call):
            if node.keywords:
                raise ValueError("Keyword arguments are not allowed")
            if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_MATH_NAMES:
                raise ValueError("Only math.* functions are allowed")
            fn = _ALLOWED_MATH_NAMES[node.func.id]
            if not callable(fn):
                raise ValueError(f"'{node.func.id}' is not a function")
            return _check_result(fn(*(_eval(arg) for arg in node.args)))
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    return _check_result(_eval(tree))


@mcp.tool()
def calculate_expression(expression: str) -> str:
    """
    Safely evaluate a mathematical expression using a restricted AST interpreter
    (no eval/exec — attribute access, imports and dunders are impossible).
    """
    try:
        val = _safe_math_eval(expression)
        return f"Result: {val}"
    except Exception as exc:
        return f"Calculation error: {str(exc)}"


# ==========================================
# 2. FastMCP Resources
# ==========================================
@mcp.resource("nexus://system/status")
def get_system_status() -> str:
    """
    Get live system health and operational parameters.
    """
    import platform
    import time

    return (
        f"Nexus System Status: OPERATIONAL\n"
        f"OS: {platform.system()} {platform.release()}\n"
        f"Timestamp: {time.time()}\n"
        f"Free Tier Mode: Active\n"
        f"Primary Provider: Groq (Llama 3.3 70B)"
    )


@mcp.resource("nexus://config/permissions")
def get_permissions_matrix() -> str:
    """
    Get current declarative tool permissions matrix.
    """
    from backend.app.services.tools.tool_gateway import tool_gateway

    return str(tool_gateway.permissions_config)


@mcp.resource("nexus://system/capabilities")
def get_advanced_capabilities() -> str:
    """
    Declare the server's advanced MCP primitive support (MD §7.7):
    Elicitations = supported (structured human input via the signed REST
    bridge), Roots = declared scopes, Sampling = not supported (a
    client-owned primitive this server never initiates).
    """
    return json.dumps({
        # Same source as the FastMCP handshake identity above, so the two can
        # no longer disagree about what this server is called.
        "server": settings.MCP_SERVER_NAME,
        "version": settings.MCP_SERVER_VERSION,
        "capabilities": {
            "tools": True,
            "resources": True,
            "prompts": True,
            "elicitations": {
                "supported": True,
                "description": "Structured human-input requests parked server-side; "
                               "answers are accepted only from the owning user via the "
                               "authenticated REST endpoint POST /api/v1/tools/elicitations/{id}/respond.",
            },
            "roots": {
                "supported": True,
                "declared_roots": [
                    {"uri": "database://conversations", "description": "Conversation/usage telemetry tables"},
                    {"uri": "qdrant://nexus_knowledge", "description": "Embedded document corpus"},
                    {"uri": "e2b://sandbox", "description": "Ephemeral code-execution microVMs (no filesystem persistence)"},
                ],
                "description": "Server declares the scopes it operates within; clients control what they expose.",
            },
            "sampling": {
                "supported": False,
                "description": "Sampling is a client-owned primitive; this server never delegates generation to a host LLM.",
            },
        },
    }, indent=2)


@mcp.tool()
async def request_user_input(conversation_id: str, message: str, schema_json: str, title: str = "Action needed") -> str:
    """
    Park a structured human-input request (MCP elicitation) against a
    conversation. The user answers through the authenticated REST endpoint;
    this tool returns the pending elicitation id (single-use, expiry-bounded).
    """
    from uuid import UUID

    from backend.app.infrastructure.database.session import async_session_factory
    from backend.app.services.tools.elicitations import ElicitationService

    try:
        schema = json.loads(schema_json)
        convo_uuid = UUID(conversation_id)
    except Exception as exc:
        return f"Error: schema_json must be valid JSON and conversation_id a UUID — {exc}"

    async with async_session_factory() as session:
        service = ElicitationService(session)
        parked = await service.park_elicitation(
            conversation_id=convo_uuid,
            schema=schema,
            message=message,
            title=title,
        )
    return (
        f"Elicitation parked (id: {parked['elicitation_id']}). "
        "The human answers via POST /api/v1/tools/elicitations/{id}/respond "
        "using their own authenticated session; the request expires "
        "automatically if unanswered."
    )


@mcp.tool()
async def create_artifact(
    user_id: str, title: str, content: str, language: str = "markdown",
    conversation_id: str | None = None, message_id: str | None = None,
) -> str:
    """
    Persist an AI-generated artifact (document/spec/code) for a user.
    Creates a versioned artifact row — later revisions keep prior content.
    """
    from uuid import UUID

    from backend.app.domain.artifact.schemas import ArtifactCreate
    from backend.app.infrastructure.database.session import async_session_factory
    from backend.app.services.artifact_service import ArtifactService

    try:
        user_uuid = UUID(user_id)
    except Exception as exc:
        return f"Error: user_id must be a UUID — {exc}"
    if not content.strip():
        return "Error: content must not be empty"
    if len(content) > 200_000:
        return "Error: content exceeds the 200_000 character limit"

    conversation_uuid = UUID(conversation_id) if conversation_id else None
    message_uuid = UUID(message_id) if message_id else None

    payload = ArtifactCreate(
        title=title, language=language, content=content,
        conversation_id=conversation_uuid, message_id=message_uuid,
    )
    async with async_session_factory() as session:
        artifact = await ArtifactService(session).create(user_uuid, payload)
    return (
        f"Artifact persisted (id: {artifact.id}, title: {artifact.title!r}, "
        f"version: {artifact.version}, language: {artifact.language})."
    )


# ==========================================
# 3. FastMCP Prompts
# ==========================================
@mcp.prompt()
def code_review_prompt(code: str, language: str = "python") -> str:
    """
    Prompt template for thorough code review and security audit.
    """
    return (
        f"Please perform a senior-level code review on the following {language} code:\n\n"
        f"```{language}\n{code}\n```\n\n"
        "Evaluate: 1. Correctness 2. Security vulnerabilities 3. Performance & edge cases."
    )


@mcp.prompt()
def research_brief_prompt(topic: str) -> str:
    """
    Prompt template for synthesizing deep research briefs.
    """
    return (
        f"Produce a comprehensive, structured research brief on: '{topic}'.\n"
        "Structure: Executive Summary, Key Findings, Market / Technical Impact, Challenges, and Recommendations."
    )
