"""
Tool Discovery & Semantic Search Service.
Implements Pattern 39: Tool Search Tool.
Enables dynamic tool discovery across large tool catalogs via semantic similarity & keyword matching.
"""
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

# Standard Built-in Tool Catalog for Nexus
DEFAULT_TOOL_CATALOG: list[dict[str, Any]] = [
    {
        "name": "web_search",
        "description": "Searches the live internet for recent information, news, documentation, and websites using Firecrawl.",
        "parameters": {"query": {"type": "string", "description": "Search query"}},
        "category": "information",
    },
    {
        "name": "code_execution",
        "description": "Executes Python code in an isolated E2B microVM sandbox, returns stdout, stderr, and generated charts.",
        "parameters": {"code": {"type": "string", "description": "Python code snippet"}},
        "category": "computation",
    },
    {
        "name": "rag_search",
        "description": "Searches user uploaded documents and knowledge base using dense and sparse hybrid vector search.",
        "parameters": {"query": {"type": "string", "description": "Search query"}},
        "category": "knowledge",
    },
    {
        "name": "eval_benchmark",
        "description": "Runs DeepEval/Ragas evaluation benchmarks on a given response turn.",
        "parameters": {"turn_id": {"type": "string", "description": "Turn identifier"}},
        "category": "evaluation",
    },
]


class ToolDiscoveryService:
    """Discovers and retrieves relevant tools for a given user task or step."""

    def __init__(self, catalog: list[dict[str, Any]] | None = None) -> None:
        self.catalog = catalog or DEFAULT_TOOL_CATALOG

    def search_tools(self, query: str, limit: int = 3) -> list[dict[str, Any]]:
        query_words = set(query.lower().split())
        scored_tools = []

        for tool in self.catalog:
            desc_words = set(f"{tool['name']} {tool['description']} {tool['category']}".lower().split())
            overlap = len(query_words.intersection(desc_words))
            scored_tools.append((overlap, tool))

        # Sort by relevance score
        scored_tools.sort(key=lambda x: x[0], reverse=True)
        results = [tool for score, tool in scored_tools if score > 0][:limit]

        # If no direct match, return all available tools up to limit
        if not results:
            results = self.catalog[:limit]

        logger.info("tool_discovery_executed", query=query, matched_count=len(results))
        return results


tool_discovery = ToolDiscoveryService()
