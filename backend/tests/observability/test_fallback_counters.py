"""Fail-open paths stay fail-open, but every activation is now counted."""

from types import SimpleNamespace

import backend.app.infrastructure.ai.litellm_client as llm_mod
import backend.app.infrastructure.cache.response_cache as cache_mod
import backend.app.services.memory.long_term as memory_mod
import backend.app.services.notifications.email as email_mod
import backend.app.services.observability.fallbacks as fallbacks_mod
import backend.app.services.tools.web_search as search_mod
import pytest
from backend.app.services.observability.metrics import MetricsCollector


def test_count_fallback_uses_the_dependency_namespace(monkeypatch):
    collector = MetricsCollector()
    monkeypatch.setattr(fallbacks_mod, "metrics_collector", collector)

    fallbacks_mod.count_fallback("mem0_add")

    assert collector.get_summary()["counters"] == {"dependency_fallback:mem0_add": 1}


@pytest.mark.asyncio
async def test_email_dev_sink_is_counted(monkeypatch):
    seen = []
    monkeypatch.setattr(email_mod, "count_fallback", seen.append)
    monkeypatch.setattr(
        email_mod,
        "settings",
        SimpleNamespace(RESEND_API_KEY=None, SMTP_USER=None, SMTP_PASSWORD=None, EMAIL_FROM="from@example.com"),
    )

    result = await email_mod.EmailService().send("to@example.com", "subject", "<p>body</p>", "body")

    assert result["channel"] == "dev"
    assert seen == ["email_dev_sink"]


@pytest.mark.asyncio
async def test_response_cache_read_failure_is_counted(monkeypatch):
    seen = []

    class BrokenCache:
        async def get(self, key):
            raise ConnectionError("redis down")

    monkeypatch.setattr(cache_mod, "count_fallback", seen.append)
    monkeypatch.setattr(
        cache_mod,
        "settings",
        SimpleNamespace(RESPONSE_CACHE_ENABLED=True, RESPONSE_CACHE_MIN_LENGTH=1),
    )

    value = await cache_mod.ResponseCache(BrokenCache()).get("user-1", "model-1", "a long enough question")

    assert value is None
    assert seen == ["response_cache_read"]


@pytest.mark.asyncio
async def test_web_search_cascade_advances_and_counts_each_failure(monkeypatch):
    seen = []
    service = search_mod.WebSearchService(tavily_key="tvly-test", firecrawl_key="fc-test")

    async def tavily_fails(*args, **kwargs):
        raise RuntimeError("tavily down")

    async def firecrawl_fails(*args, **kwargs):
        raise RuntimeError("firecrawl down")

    async def ddg_empty(*args, **kwargs):
        return []

    monkeypatch.setattr(search_mod, "count_fallback", seen.append)
    monkeypatch.setattr(service, "_search_tavily", tavily_fails)
    monkeypatch.setattr(service, "_search_firecrawl", firecrawl_fails)
    monkeypatch.setattr(service, "_search_duckduckgo", ddg_empty)

    assert await service.search("hello") == []
    assert seen == ["web_search:tavily_to_firecrawl", "web_search:firecrawl_to_ddg"]


@pytest.mark.asyncio
async def test_mem0_add_and_search_failures_are_counted(monkeypatch):
    seen = []

    class BrokenMem0:
        async def add(self, **kwargs):
            raise RuntimeError("mem0 down")

        async def search(self, **kwargs):
            raise RuntimeError("mem0 down")

    monkeypatch.setattr(memory_mod, "count_fallback", seen.append)
    service = memory_mod.LongTermMemoryService(client=BrokenMem0())

    assert await service.add("remember this", "user-1") is None
    assert await service.add_from_conversation([{"role": "user", "content": "hi"}], "user-1") == []
    assert await service.search("anything", "user-1") == []
    assert seen == ["mem0_manual_add", "mem0_add", "mem0_search"]


def test_llm_provider_fallback_is_counted(monkeypatch):
    seen = []
    monkeypatch.setattr(llm_mod, "count_fallback", seen.append)

    llm_mod._record_provider_fallback("complex_reasoning", "gemini-3.5-flash", "fast_text")

    assert seen == ["llm_provider_fallback"]
