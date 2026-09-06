"""
Web Search and Web Scraping Service.
Implements the priority ladder:
  1. Tavily Search API (primary, when TAVILY_API_KEY is configured)
  2. Firecrawl v2 Search API (secondary, when FIRECRAWL_API_KEY is configured)
  3. DuckDuckGo (zero-key resilient fallback)
"""
import asyncio
from typing import Any

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_MAX_TOKENS = 4000


class WebSearchResult:
    def __init__(self, title: str, url: str, snippet: str, content: str = ""):
        self.title = title
        self.url = url
        self.snippet = snippet
        self.content = content

    def to_dict(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "url": self.url,
            "snippet": self.snippet,
            "content": self.content,
        }


class WebSearchService:
    """
    Combines Tavily / Firecrawl search APIs with a DuckDuckGo free-search fallback.
    """

    def __init__(
        self,
        tavily_key: str | None = None,
        firecrawl_key: str | None = None,
    ):
        self.tavily_key = tavily_key if tavily_key is not None else settings.TAVILY_API_KEY
        self.firecrawl_key = (
            firecrawl_key if firecrawl_key is not None else settings.FIRECRAWL_API_KEY
        )

    async def search(self, query: str, max_results: int = 5) -> list[dict[str, Any]]:
        """
        Executes a web search using the priority ladder:
        Tavily → Firecrawl → DuckDuckGo.
        """
        # 1. Primary: Tavily Search API (clean agentic results, no boilerplate)
        if self.tavily_key:
            try:
                results = await self._search_tavily(query, max_results)
                if results:
                    logger.info("tavily_search_success", query=query, count=len(results))
                    return results
            except Exception as exc:
                logger.warning("tavily_search_failed_falling_back", error=str(exc))

        # 2. Secondary: Firecrawl Native Search API
        if self.firecrawl_key and not self.firecrawl_key.startswith("fc_placeholder"):
            try:
                results = await self._search_firecrawl(query, max_results)
                if results:
                    logger.info("firecrawl_search_success", query=query, count=len(results))
                    return results
            except Exception as exc:
                logger.warning("firecrawl_search_failed_falling_back_to_ddg", error=str(exc))

        # 3. Resilient Fallback: DuckDuckGo free search
        try:
            results = await self._search_duckduckgo(query, max_results)
            if results:
                logger.info("duckduckgo_search_success", query=query, results_count=len(results))
                return results
        except Exception as exc:
            logger.warning("duckduckgo_search_failed", error=str(exc))

        return []

    async def _search_tavily(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """Calls the Tavily REST API directly via async HTTP."""
        import httpx

        payload = {
            "api_key": self.tavily_key,
            "query": query,
            "max_results": max_results,
            "search_depth": "basic",
            "include_answer": False,
            "include_raw_content": False,
            "max_tokens": TAVILY_MAX_TOKENS,
        }
        async with httpx.AsyncClient(timeout=15.0) as client:
            response = await client.post(TAVILY_SEARCH_URL, json=payload)
            response.raise_for_status()
            data = response.json()

        results: list[dict[str, Any]] = []
        for item in data.get("results", []):
            raw = item.get("raw_content") or ""
            snippet = item.get("content") or raw[:300]
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": snippet,
                "content": raw,
            })
        return results

    async def _search_firecrawl(self, query: str, max_results: int) -> list[dict[str, Any]]:
        """Calls the Firecrawl v2 search API."""
        from firecrawl import FirecrawlApp

        app = FirecrawlApp(api_key=self.firecrawl_key)
        search_res = app.search(query=query, params={"limit": max_results})
        if not search_res or not isinstance(search_res, dict):
            return []

        results: list[dict[str, Any]] = []
        for item in search_res.get("data", []):
            results.append({
                "title": item.get("title", ""),
                "url": item.get("url", ""),
                "snippet": item.get("description", "") or item.get("markdown", "")[:300],
                "content": item.get("markdown", ""),
            })
        return results

    async def _search_duckduckgo(self, query: str, max_results: int, retries: int = 3) -> list[dict[str, Any]]:
        """
        Calls the DuckDuckGo search via the `ddgs` metasearch aggregator.

        DDG bot-fingerprints this network intermittently (empty results / timeouts),
        so we retry with backoff across a few engine backends before giving up.
        """
        from ddgs import DDGS
        from ddgs.exceptions import DDGSException

        ddgs = DDGS()
        for attempt in range(1, retries + 1):
            try:
                raw_results = await asyncio.to_thread(
                    ddgs.text, query, max_results=max_results, backend="auto", safesearch="moderate"
                )
                if not raw_results:
                    logger.warning(
                        "duckduckgo_empty_retrying", query=query, attempt=attempt, retries=retries
                    )
                    await asyncio.sleep(attempt)
                    continue
                return [
                    {
                        "title": r.get("title", ""),
                        "url": r.get("href", ""),
                        "snippet": r.get("body", ""),
                        "content": "",
                    }
                    for r in raw_results
                ]
            except DDGSException as exc:
                logger.warning("duckduckgo_blocked_retrying", query=query, attempt=attempt, error=str(exc))
                await asyncio.sleep(attempt)
            except Exception as exc:
                logger.warning("duckduckgo_error_retrying", query=query, attempt=attempt, error=str(exc))
                await asyncio.sleep(attempt)
        return []

    async def scrape_url(self, url: str) -> dict[str, Any]:
        """
        Scrapes a URL and converts HTML into clean, LLM-ready markdown using Firecrawl v2.
        """
        if self.firecrawl_key and not self.firecrawl_key.startswith("fc_placeholder"):
            try:
                from firecrawl import FirecrawlApp

                app = FirecrawlApp(api_key=self.firecrawl_key)
                scrape_res = app.scrape_url(url, params={"formats": ["markdown"]})
                markdown_content = scrape_res.get("markdown", "")
                title = scrape_res.get("metadata", {}).get("title", url)
                return {
                    "url": url,
                    "title": title,
                    "content": markdown_content,
                    "success": True,
                }
            except Exception as exc:
                logger.warning("firecrawl_scrape_failed_falling_back_to_httpx", url=url, error=str(exc))

        # Fallback to basic httpx + html-to-markdown if Firecrawl is unavailable
        try:
            import html2text
            import httpx

            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url, headers={"User-Agent": "NexusAI/1.0"})
                if response.status_code == 200:
                    h = html2text.HTML2Text()
                    h.ignore_links = False
                    h.ignore_images = True
                    text = h.handle(response.text)
                    return {
                        "url": url,
                        "title": url,
                        "content": text[:15000],  # Limit content size
                        "success": True,
                    }
        except Exception as exc:
            logger.error("basic_http_scrape_failed", url=url, error=str(exc))

        return {
            "url": url,
            "title": url,
            "content": "Unable to scrape webpage content.",
            "success": False,
        }


web_search_service = WebSearchService()
