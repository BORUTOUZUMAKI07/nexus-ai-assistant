"""
MCP Client and Proxy Integration.
Connects to external MCP servers and translates tools into OpenAI / LiteLLM function calling definitions.
"""
from typing import Any

import structlog

logger = structlog.get_logger(__name__)


class MCPClientManager:
    """
    Manages connections to remote or local Model Context Protocol (MCP) servers.
    """

    def __init__(self):
        self.connected_servers: dict[str, Any] = {}

    async def list_available_tools(self) -> list[dict[str, Any]]:
        """
        Discovers tools across connected MCP servers and translates them to OpenAI tool calling format.
        """
        # Internal fastmcp tools
        from backend.app.mcp.server import mcp

        tools_list = []
        try:
            # FastMCP tools
            server_tools = await mcp.get_tools()
            for t in server_tools:
                tools_list.append({
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": t.parameters or {"type": "object", "properties": {}},
                    },
                })
        except Exception as exc:
            logger.warning("mcp_tools_discovery_fallback", error=str(exc))
            # Fallback tool definitions
            tools_list = [
                {
                    "type": "function",
                    "function": {
                        "name": "web_search",
                        "description": "Search the web for up to date information.",
                        "parameters": {
                            "type": "object",
                            "properties": {"query": {"type": "string"}},
                            "required": ["query"],
                        },
                    },
                },
                {
                    "type": "function",
                    "function": {
                        "name": "execute_python_code",
                        "description": "Execute Python code in an isolated sandbox.",
                        "parameters": {
                            "type": "object",
                            "properties": {"code": {"type": "string"}},
                            "required": ["code"],
                        },
                    },
                },
            ]

        return tools_list


mcp_client = MCPClientManager()
