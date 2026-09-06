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

import structlog

logger = structlog.get_logger(__name__)

# Initialize FastMCP Server
mcp = FastMCP("Nexus-MCP-Server")


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


@mcp.tool()
def calculate_expression(expression: str) -> str:
    """
    Safely evaluate a mathematical expression.
    """
    import math

    allowed = {k: v for k, v in math.__dict__.items() if not k.startswith("__")}
    try:
        val = eval(expression, {"__builtins__": None}, allowed)
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
