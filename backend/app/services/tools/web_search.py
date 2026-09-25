"""
Web Search and Web Scraping Service.
Implements the priority ladder:
  1. Tavily Search API (primary, when TAVILY_API_KEY is configured)
  2. Firecrawl v2 Search API (secondary, when FIRECRAWL_API_KEY is configured)
  3. DuckDuckGo (zero-key resilient fallback)
"""
import asyncio
import ipaddress
import socket
from typing import Any
from urllib.parse import urlparse

import structlog
from backend.app.core.config import settings

logger = structlog.get_logger(__name__)

TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_MAX_TOKENS = 4000

# Protected network ranges an SSRF-mitigated fetch must never connect to.
_PRIVATE_NETS = [
    ipaddress.ip_network("0.0.0.0/8"),
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("100.64.0.0/10"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.0.0.0/24"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("198.18.0.0/15"),
    ipaddress.ip_network("224.0.0.0/4"),
    ipaddress.ip_network("240.0.0.0/4"),
    ipaddress.ip_network("::/128"),       # unspecified
    ipaddress.ip_network("::1/128"),      # loopback
    ipaddress.ip_network("fc00::/7"),     # unique local
    ipaddress.ip_network("fe80::/10"),    # link-local
    ipaddress.ip_network("ff00::/8"),     # multicast
]
_SSRF_BLOCKED_HOSTS = {"localhost", "localhost.localdomain", "metadata.google.internal"}


def _is_reserved_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return any(ip in net for net in _PRIVATE_NETS)


async def _validate_public_url(url: str) -> str:
    """
    SSRF guard: ensures ``url`` is http(s), points at a non-reserved hostname/IP,
    and that the host's first DNS resolution is a globally routable address.
    Returns the sanitized URL or raises ValueError.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http/https URLs are allowed (got scheme '{parsed.scheme}')")
    host = parsed.hostname or ""
    if not host:
        raise ValueError("URL must include a hostname")

    hostname = host.rstrip(".").lower()
    if hostname in _SSRF_BLOCKED_HOSTS or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise ValueError(f"Host '{hostname}' is not a publicly routable destination")

    # Literal IP fast-path: reject reserved/private/protected ranges outright.
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None
    if ip is not None:
        if _is_reserved_ip(ip) or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise ValueError(f"Host '{hostname}' is a private/reserved address")
        return url

    # Hostname → resolve and check every returned address.
    resolved = await asyncio.to_thread(socket.getaddrinfo, hostname, None)
    for _family, _socktype, _proto, _canon, sockaddr in resolved:
        try:
            addr = ipaddress.ip_address(sockaddr[0])
        except ValueError:
            continue
        if _is_reserved_ip(addr) or addr.is_loopback or addr.is_link_local or addr.is_reserved:
            raise ValueError(f"Host '{hostname}' resolves to a private/reserved address ({addr})")
    return url


async def _fetch_with_ssrf_guard(url: str, *, timeout: float = 10.0, max_bytes: int = 1_500_000) -> tuple[int, str, bytes]:
    """
    Fetches ``url`` while re-validating every redirect against the SSRF guard.
    Returns (status_code, final_url, body).
    """
    import httpx

    current = await _validate_public_url(url)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        for _ in range(10):  # hard redirect cap
            response = await client.get(current, headers={"User-Agent": "NexusAI/1.0"})
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    return response.status_code, current, response.content
                from urllib.parse import urljoin

                current = await _validate_public_url(urljoin(current, location))
                continue
            if len(response.content) > max_bytes:
                return response.status_code, current, response.content[:max_bytes]
            return response.status_code, current, response.content
    return response.status_code, current, response.content


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
        # Firecrawl SDK is synchronous — never block the event loop.
        search_res = await asyncio.to_thread(app.search, query=query, params={"limit": max_results})
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
                # SDK v1: keyword args, not params dict. Blocking SDK → run off-loop.
                scrape_res = await asyncio.to_thread(
                    app.scrape_url, url, formats=["markdown"]
                )
                if hasattr(scrape_res, "markdown"):
                    markdown_content = scrape_res.markdown or ""
                    title = (scrape_res.metadata or {}).get("title", url) if hasattr(scrape_res, "metadata") else url
                else:
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

        # Fallback to basic httpx + html-to-markdown if Firecrawl is unavailable.
        # Every hop is SSRF-validated (scheme, host, DNS resolution, redirects).
        try:
            import html2text

            status_code, final_url, content = await _fetch_with_ssrf_guard(url)
            if status_code == 200:
                h = html2text.HTML2Text()
                h.ignore_links = False
                h.ignore_images = True
                text = h.handle(content.decode("utf-8", errors="replace"))
                return {
                    "url": final_url,
                    "title": final_url,
                    "content": text[:15000],  # Limit content size
                    "success": True,
                }
            logger.warning("basic_http_scrape_non_200", url=url, status_code=status_code)
        except Exception as exc:
            logger.error("basic_http_scrape_failed", url=url, error=str(exc))

        return {
            "url": url,
            "title": url,
            "content": "Unable to scrape webpage content.",
            "success": False,
        }


web_search_service = WebSearchService()
