"""
Researcher Subagent.
Specializes in search, web browsing, and multi-source evidence synthesis.
"""
from typing import Any

import structlog
from backend.app.infrastructure.ai.litellm_client import ai_client
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

        # 2. Scrape top result for in-depth content
        scraped_contents: list[str] = []
        for res in search_results[:2]:
            url = res.get("url")
            if url:
                scrape_data = await web_search_service.scrape_url(url)
                if scrape_data.get("success") and scrape_data.get("content"):
                    scraped_contents.append(f"Source ({url}):\n{scrape_data['content'][:4000]}")

        # 3. Synthesize summary using LLM
        context = "\n\n---\n\n".join(scraped_contents) if scraped_contents else str(search_results)
        synthesis_prompt = f"Synthesize findings on the following query based on these retrieved sources:\n\nTopic: {topic}\n\nSources:\n{context}"

        response = await ai_client.completion(
            messages=[
                {"role": "system", "content": RESEARCHER_SYSTEM_PROMPT},
                {"role": "user", "content": synthesis_prompt},
            ],
            model="llama-3.3-70b-versatile",
            temperature=0.2,
        )

        return {
            "subagent": "researcher",
            "topic": topic,
            "synthesis": response,
            "sources": [r.get("url") for r in search_results if r.get("url")],
        }


researcher_subagent = ResearcherSubagent()
