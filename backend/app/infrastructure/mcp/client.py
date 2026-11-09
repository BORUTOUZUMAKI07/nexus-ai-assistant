"""
MCP (Model Context Protocol) client.

Connects Nexus to external MCP servers (filesystem, git, Linear, etc.) via
stdio or SSE. Discovered tools are registered into the tool_gateway catalog so
the orchestrator can invoke them like built-in tools.

Implements a minimal MCP client: initialize, list tools, call tools. Does not
implement sampling, roots, or notifications — those can be added as needed.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MCPServerConfig:
    name: str
    command: str | None = None        # stdio server command
    args: list[str] = field(default_factory=list)
    env: dict[str, str] = field(default_factory=dict)
    url: str | None = None            # SSE server URL
    headers: dict[str, str] = field(default_factory=dict)
    enabled: bool = True


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: dict[str, Any]
    server_name: str


class MCPClient:
    """
    Minimal MCP client supporting stdio and SSE transports.

    Each server process is kept alive for the duration of the client. Tool
    calls are routed to the owning server by name (tool names are prefixed
    with the server name to avoid collisions, e.g. `filesystem.read_file`).
    """

    def __init__(self) -> None:
        self._servers: dict[str, MCPServerConfig] = {}
        self._tools: dict[str, MCPTool] = {}  # prefixed_name -> tool
        self._processes: dict[str, subprocess.Popen] = {}
        self._initialized = False

    def add_server(self, config: MCPServerConfig) -> None:
        """Register an MCP server config. Call start() to connect."""
        self._servers[config.name] = config

    async def start_all(self) -> None:
        """Connect to all enabled servers and list their tools."""
        for name, config in self._servers.items():
            if not config.enabled:
                continue
            try:
                await self._start_server(name, config)
            except Exception as exc:  # pragma: no cover - defensive
                # One dead server must not kill all the others.
                import logging

                logging.getLogger(__name__).warning(
                    "mcp_server_start_failed", server=name, error=str(exc)
                )
        self._initialized = True

    async def _start_server(self, name: str, config: MCPServerConfig) -> None:
        if config.command:
            proc = await asyncio.create_subprocess_exec(
                config.command,
                *config.args,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env={**config.env},
            )
            self._processes[name] = proc
            await self._initialize_stdio(name, proc)
        elif config.url:
            await self._initialize_sse(name, config.url, config.headers)
        else:
            raise ValueError(f"MCP server {name} has no command or url")

    async def _initialize_stdio(
        self, name: str, proc: asyncio.subprocess.Process
    ) -> None:
        assert proc.stdin and proc.stdout
        init_msg = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "nexus-ai-assistant", "version": "1.0.0"},
            },
        }
        proc.stdin.write((json.dumps(init_msg) + "\n").encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server {name} did not respond to initialize")
        resp = json.loads(line.decode())
        if "error" in resp:
            raise RuntimeError(f"MCP initialize error: {resp['error']}")

        # List tools
        list_msg = {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/list",
            "params": {},
        }
        proc.stdin.write((json.dumps(list_msg) + "\n").encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server {name} did not respond to tools/list")
        resp = json.loads(line.decode())
        tools = resp.get("result", {}).get("tools", [])
        for tool in tools:
            prefixed = f"{name}.{tool['name']}"
            self._tools[prefixed] = MCPTool(
                name=prefixed,
                description=tool.get("description", ""),
                input_schema=tool.get("inputSchema", {}),
                server_name=name,
            )

    async def _initialize_sse(self, name: str, url: str, headers: dict[str, str]) -> None:
        # SSE transport would use an HTTP client (httpx/aiohttp). Kept as a
        # placeholder so the import path is clear; stdio is the primary
        # transport for local servers.
        raise NotImplementedError("SSE transport not yet implemented")

    def list_tools(self) -> list[MCPTool]:
        """Return all discovered tools across all servers."""
        return list(self._tools.values())

    def get_tool(self, name: str) -> MCPTool | None:
        """Get a tool by prefixed name (e.g. 'filesystem.read_file')."""
        return self._tools.get(name)

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        """Call a tool on its owning server via the appropriate transport."""
        tool = self.get_tool(name)
        if not tool:
            raise ValueError(f"Unknown MCP tool: {name}")

        server_config = self._servers[tool.server_name]
        if server_config.command:
            return await self._call_tool_stdio(tool.server_name, name, arguments)
        raise NotImplementedError(f"Call not implemented for {server_config.url}")

    async def _call_tool_stdio(
        self, server_name: str, tool_name: str, arguments: dict[str, Any]
    ) -> Any:
        proc = self._processes.get(server_name)
        if not proc or not proc.stdin or not proc.stdout:
            raise RuntimeError(f"MCP server {server_name} not running")

        call_msg = {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": tool_name.split(".", 1)[1], "arguments": arguments},
        }
        proc.stdin.write((json.dumps(call_msg) + "\n").encode())
        await proc.stdin.drain()
        line = await proc.stdout.readline()
        if not line:
            raise RuntimeError(f"MCP server {server_name} did not respond to tools/call")
        resp = json.loads(line.decode())
        if "error" in resp:
            raise RuntimeError(f"MCP tool error: {resp['error']}")
        return resp.get("result")

    async def shutdown(self) -> None:
        """Shut down all server processes."""
        for proc in self._processes.values():
            try:
                proc.terminate()
                proc.wait(timeout=5)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass
        self._processes.clear()
        self._initialized = False


# Singleton instance for the app to use.
mcp_client = MCPClient()
