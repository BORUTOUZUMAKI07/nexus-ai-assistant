"""
Researcher Subagent.
Specializes in search, web browsing, and multi-source evidence synthesis.
"""
from typing import Any

import structlog
from backend.app.core.config import settings
from backend.app.infrastructure.ai.litellm_client import ai_client
from backend.app.services.rag.base import needs_scraping
from backend.app.services.tools.web_search import web_search_service

logger = structlog.get_logger(__name__)

RESEARCHER_SYSTEM_PROMPT = """You are the Nexus Research Specialist.
Your job is to search the web, inspect sources, and synthesize factual, objective findings.
Always cite your sources and extract key facts with maximum precision."""


class ResearcherSubagent:
    """
    Subagent that runs targeted research queries and formats findings.
    """

    async def execute(self, topic: str, max_sources: int = 3) -> dict[str, Any]:
        logger.info("researcher_subagent_starting", topic=topic)

        # 1. Search for information
        search_results = await web_search_service.search(topic, max_results=max_sources)

        # 2. Fetch full text for results that are only a preview.
        #
        #    This used to scrape the top two results unconditionally, which
        #    spent a network round trip (and risked a block page) on results
        #    that already carried a full body. `requires_scraping` is declared
        #    by each provider on the result itself, so the decision is data
        #    rather than a guess from the length of a string.
        #
        #    The snippet fallback matters as much as the skip: scrape_url now
        #    rejects block pages and unextracted PDFs, so a "successful"
        #    scrape is no longer guaranteed. Without this, adding the
        #    content-shape guard would have *removed* evidence rather than
        #    filtering bad evidence out of it.
        evidence: list[str] = []
        scrape_attempts = 0
        scrape_rejections = 0
        for res in search_results:
            url = res.get("url")
            body = res.get("content") or res.get("snippet") or ""
            if not url:
                continue

            content = ""
            if needs_scraping(res):
                scrape_attempts += 1
                scrape_data = await web_search_service.scrape_url(url)
                if scrape_data.get("success") and scrape_data.get("content"):
                    content = str(scrape_data["content"])[:4000]
                else:
                    scrape_rejections += 1
                    logger.info(
                        "researcher_scrape_rejected",
                        reason=scrape_data.get("rejection_reason", "unknown"),
                        url=url,
                    )
            elif body:
                content = str(body)[:4000]

            if content:
                evidence.append(f"Source ({url}):\n{content}")
            elif body:
                evidence.append(f"Source ({url}, snippet only):\n{str(body)[:600]}")

        logger.info(
            "researcher_sources_gathered",
            total=len(search_results),
            with_evidence=len(evidence),
            scrape_attempts=scrape_attempts,
            scrape_rejections=scrape_rejections,
        )

        # 3. Synthesize summary using LLM
        context = "\n\n---\n\n".join(evidence) if evidence else str(search_results)
        synthesis_prompt = f"Synthesize findings on the following query based on these retrieved sources:\n\nTopic: {topic}\n\nSources:\n{context}"

        response = await ai_client.completion(
            messages=[
                {"role": "system", "content": RESEARCHER_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            model=settings.FAST_MODEL,
            temperature=0.2,
            max_tokens=800,
        )

        return {
            "subagent": "researcher",
            "topic": topic,
            "synthesis": response,
            "sources": [r.get("url") for r in search_results if r.get("url")],
        }


researcher_subagent = ResearcherSubagent()
