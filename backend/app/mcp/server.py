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
                def __init__(self, name: str):
                    self.name = name
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
import math
import operator

import structlog

logger = structlog.get_logger(__name__)

# Initialize FastMCP Server
mcp = FastMCP("Nexus-MCP-Server")

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

    def _checked(value: Any) -> float | int:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Calculation must produce a numeric result")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Result must be finite")
        if abs(value) > 1e100:
            raise ValueError("Result magnitude exceeds the supported limit")
        return value

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
                if abs(right) > 100:
                    raise ValueError("Exponent magnitude exceeds 100")
                if abs(left) > 1e100:
                    raise ValueError("Base magnitude exceeds the supported limit")
            return _checked(op_fn(left, right))
        if isinstance(node, ast.UnaryOp):
            op_fn = _UNARY_OPS.get(type(node.op))
            if op_fn is None:
                raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
            return _checked(op_fn(_eval(node.operand)))
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
            return _checked(fn(*(_eval(arg) for arg in node.args)))
        raise ValueError(f"Unsupported expression element: {type(node).__name__}")

    return _checked(_eval(tree))


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
