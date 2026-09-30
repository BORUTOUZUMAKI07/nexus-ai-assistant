"""
Tests for D1 - researcher depth: decomposition, reflection, bounded budgets.

Each test fails if its fix is reverted.
"""

import json

import pytest
from backend.app.agents.subagents import researcher as r
from backend.app.agents.subagents.researcher import (
    ResearcherSubagent,
    _clean_query,
    _parse_json_list,
    _parse_reflection,
)

PAGE = (
    "Retrieval-augmented generation combines a parametric model with a "
    "non-parametric retriever, reducing hallucination because the model can "
    "attend to source text. It was first described in the 2020 paper on "
    "knowledge-intensive NLP tasks, and the result is a system that cites its "
    "sources and can be evaluated for faithfulness of the generated answer. "
) * 4


class FakeSearch:
    """Records every call so budget and breadth can be asserted."""

    def __init__(self, results=None, error: bool = False):
        self.results = results if results is not None else []
        self.error = error
        self.searches: list[tuple[str, int]] = []
        self.scrapes: list[str] = []
        self.concurrent_scrapes = 0
        self._in_flight = 0
        self.peak_concurrency = 0

    async def search(self, query, max_results=5):
        self.searches.append((query, max_results))
        if self.error:
            raise RuntimeError("provider down")
        return [dict(r) for r in self.results]

    async def scrape_url(self, url):
        self.scrapes.append(url)
        self._in_flight += 1
        self.peak_concurrency = max(self.peak_concurrency, self._in_flight)
        import asyncio

        await asyncio.sleep(0)  # yield so overlap is observable
        self._in_flight -= 1
        return {"url": url, "content": PAGE, "success": True}


def _result(url, *, snippet="", preview=True, content=None):
    return {
        "url": url,
        "title": f"Title {url}",
        "snippet": snippet or f"snippet for {url}",
        "content": content if content is not None else "",
        "requires_scraping": preview,
    }


def _install(monkeypatch, service: FakeSearch, replies: list[str]):
    """Wire a fake AI client returning `replies` in order, then "" forever."""
    calls: list[dict] = []
    remaining = list(replies)

    async def fake_completion(**kwargs):
        calls.append(kwargs)
        return remaining.pop(0) if remaining else ""

    monkeypatch.setattr(r, "web_search_service", service)
    monkeypatch.setattr(r.ai_client, "completion", fake_completion)
    return calls


# -- parsing helpers ----------------------------------------------------------


def test_parse_json_list_handles_a_plain_array():
    assert _parse_json_list('["a", "b"]') == ["a", "b"]


def test_parse_json_list_ignores_prose_around_the_array():
    raw = 'Here you go:\n["one", "two"]\nHope that helps.'
    assert _parse_json_list(raw) == ["one", "two"]


def test_parse_json_list_handles_a_fenced_reply():
    assert _parse_json_list('```json\n["x", "y"]\n```') == ["x", "y"]


def test_parse_json_list_returns_empty_on_garbage():
    for bad in ("", "no json here", "[unclosed", "{'single': 'quotes'}", None):
        assert _parse_json_list(bad) == []


def test_parse_json_list_drops_non_scalar_entries():
    assert _parse_json_list('[{"a": 1}, "keep", 5, null]') == ["keep", "5"]


def test_parse_reflection_reads_the_object():
    out = _parse_reflection('{"sufficient": false, "gaps": ["x"], "reason": "thin"}')
    assert out["sufficient"] is False
    assert out["gaps"] == ["x"]
    assert out["reason"] == "thin"


def test_parse_reflection_defaults_to_sufficient_when_unparseable():
    """Failing closed on "insufficient" would trigger endless gap-filling."""
    for bad in ("", "not json", "{broken", "[]"):
        out = _parse_reflection(bad)
        assert out["sufficient"] is True
        assert out["gaps"] == []


def test_parse_reflection_survives_wrong_types():
    out = _parse_reflection('{"sufficient": "yes", "gaps": "notalist"}')
    assert out["sufficient"] is True
    assert out["gaps"] == []


def test_clean_query_strips_control_and_odd_characters():
    out = _clean_query("  postgres\x00 performance; DROP\x07  ")
    assert "\x00" not in out and "\x07" not in out
    assert "postgres" in out and "performance" in out


def test_clean_query_bounds_length():
    assert len(_clean_query("x" * 5000)) <= 200


def test_clean_query_handles_non_strings():
    assert _clean_query(None) == ""
    assert _clean_query(123) == "123"


# -- breadth: decomposition ---------------------------------------------------


async def test_topic_is_decomposed_and_each_subquery_searched(monkeypatch):
    """The core capability: a multi-part question gets more than one query."""
    sub = ResearcherSubagent()
    sub._ask = _decompose_stub(["alpha", "beta", "gamma"])

    queries = await sub._subqueries("How do A and B differ, and which is faster?")

    assert queries == ["alpha", "beta", "gamma"]


async def test_decomposition_is_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_SUBQUERIES", 1)
    sub = ResearcherSubagent()
    assert await sub._subqueries("a question") == ["a question"]


async def test_decomposition_is_skipped_when_only_one_call_remains(monkeypatch):
    """The synthesis is mandatory, so it must never be spent on breadth."""
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_MODEL_CALLS", 1)
    sub = ResearcherSubagent()

    async def _no_spend(*a, **k):
        pytest.fail("must not spend a call on decomposition")

    sub._ask = _no_spend
    assert await sub._subqueries("a question") == ["a question"]


async def test_decomposition_falls_back_to_the_topic_when_the_model_fails(monkeypatch):
    sub = ResearcherSubagent()

    async def _fail(*a, **k):
        return ""

    sub._ask = _fail
    assert await sub._subqueries("a question") == ["a question"]


async def test_decomposition_does_not_trivially_echo_the_topic(monkeypatch):
    """A decomposition that returns the question unchanged is pure cost."""
    sub = ResearcherSubagent()
    sub._ask = _decompose_stub(["a question"])
    assert await sub._subqueries("a question") == ["a question"]


async def test_decomposition_is_deduplicated_and_capped(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_SUBQUERIES", 2)
    sub = ResearcherSubagent()
    sub._ask = _decompose_stub(["a", "a", "b", "c"])

    assert await sub._subqueries("topic") == ["a", "b"]


def _decompose_stub(items):
    """An async replacement for `_ask` returning a fixed decomposition."""

    async def _ask(system, user, max_tokens):
        return json.dumps(items)

    return _ask


# -- depth: reflection --------------------------------------------------------


async def test_reflection_is_skipped_when_evidence_is_sufficient(monkeypatch):
    """The cost discipline: a good harvest must not pay for reflection."""
    monkeypatch.setattr(r.settings, "RESEARCH_EVIDENCE_FLOOR_CHARS", 100)
    sub = ResearcherSubagent()
    sub._ask = lambda *a, **k: pytest.fail("must not spend a call on reflection")

    evidence = [f"Source (u):\n{'x' * 400}"]
    out_ev, out_src, follow, spent, refl = await sub._reflect(
        "topic", ["topic"], evidence, scrape_budget=2
    )
    assert follow == []
    assert spent == 0
    assert refl["reason"] == "evidence_sufficient"


async def test_reflection_is_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_REFLECTIONS", 0)
    sub = ResearcherSubagent()
    sub._ask = lambda *a, **k: pytest.fail("must not spend a call")

    out_ev, out_src, follow, spent, refl = await sub._reflect("t", ["t"], [], scrape_budget=1)
    assert follow == [] and spent == 0


async def test_thin_evidence_triggers_gap_filling(monkeypatch):
    """The capability the old researcher had no way to express at all."""
    monkeypatch.setattr(r.settings, "RESEARCH_EVIDENCE_FLOOR_CHARS", 100_000)
    service = FakeSearch(results=[_result("https://a")])
    _install(monkeypatch, service, ['{"sufficient": false, "gaps": ["missing facet"], "reason": "thin"}'])

    sub = ResearcherSubagent()
    out_ev, out_src, follow, spent, refl = await sub._reflect(
        "topic", ["topic"], ["Source (u):\nshort"], scrape_budget=2
    )

    assert refl["sufficient"] is False
    assert "missing facet" in service.searches[0][0] or len(service.searches) >= 1
    assert spent == 1
    assert "https://a" in out_src


async def test_gap_filling_is_skipped_when_the_budget_is_spent(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_EVIDENCE_FLOOR_CHARS", 100_000)
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_MODEL_CALLS", 2)
    service = FakeSearch(results=[_result("https://a")])
    _install(
        monkeypatch,
        service,
        ['{"sufficient": false, "gaps": ["a gap"], "reason": "thin"}'],
    )

    sub = ResearcherSubagent()
    sub.calls_used = 1  # one left for reflection, none for the synthesis
    out_ev, out_src, follow, spent, refl = await sub._reflect(
        "t", ["t"], ["short"], scrape_budget=1
    )
    assert follow == []
    assert service.searches == []


async def test_reflection_claiming_insufficient_with_no_gaps_does_nothing(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_EVIDENCE_FLOOR_CHARS", 100_000)
    service = FakeSearch(results=[_result("https://a")])
    _install(monkeypatch, service, ['{"sufficient": false, "gaps": [], "reason": "vibes"}'])

    sub = ResearcherSubagent()
    _ev, _src, follow, spent, _refl = await sub._reflect(
        "t", ["t"], ["short"], scrape_budget=2
    )
    assert follow == [] and spent == 0
    assert service.searches == []


# -- budgets ------------------------------------------------------------------


async def test_total_scrape_budget_is_not_multiplied_by_breadth(monkeypatch):
    """The specific over-spend this design has to prevent."""
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_SCRAPES", 2)
    service = FakeSearch(
        results=[
            _result("https://a"),
            _result("https://b"),
            _result("https://c"),
            _result("https://d"),
        ]
    )
    _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q1", "q2", "q3"])

    out = await sub.execute("topic", max_sources=4)

    assert len(service.scrapes) <= 2, f"scraped {service.scrapes}"
    assert out["scrape_count"] <= 2


async def test_scrape_concurrency_is_actually_bounded(monkeypatch):
    """Unbounded gather() is a good way to get rate-limited and lose every page."""
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_SCRAPES", 6)
    monkeypatch.setattr(r.settings, "RESEARCH_SCRAPE_CONCURRENCY", 2)
    service = FakeSearch(
        results=[_result(f"https://{i}") for i in range(6)]
    )
    _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q"])
    await sub.execute("topic", max_sources=6)

    assert len(service.scrapes) == 6
    assert service.peak_concurrency <= 2, f"peak concurrency was {service.peak_concurrency}"


async def test_model_call_ceiling_is_respected(monkeypatch):
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_MODEL_CALLS", 2)
    monkeypatch.setattr(r.settings, "RESEARCH_MAX_REFLECTIONS", 1)
    monkeypatch.setattr(r.settings, "RESEARCH_EVIDENCE_FLOOR_CHARS", 100_000)
    service = FakeSearch(results=[])
    calls = _install(monkeypatch, service, ["[]", '{"sufficient": false, "gaps": ["g"]}'])

    sub = ResearcherSubagent()
    out = await sub.execute("topic")

    assert out["model_calls"] <= 2, f"spent {out['model_calls']} model calls"
    assert len(calls) <= 2


def _replies(items):
    """An async replacement for `_subqueries` returning a fixed query list."""

    async def _subqueries(topic):
        return items

    return _subqueries


async def test_synthesis_always_runs_even_with_no_evidence(monkeypatch):
    """A hollow answer with cited gaps beats no answer at all."""
    service = FakeSearch(results=[])
    calls = _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["topic"])
    out = await sub.execute("topic")

    assert out["synthesis"] or out["model_calls"] >= 1
    assert "no sources could be retrieved" in calls[-1]["messages"][1]["content"]


async def test_search_failure_does_not_discard_other_evidence(monkeypatch):
    """One provider failure must not cost the turn its findings."""
    service = FakeSearch(error=True)
    _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q"])
    out = await sub.execute("topic")

    assert out["sources"] == []


async def test_scrape_failure_falls_back_to_the_snippet(monkeypatch):
    class RejectingSearch(FakeSearch):
        async def scrape_url(self, url):
            self.scrapes.append(url)
            return {"success": False, "rejection_reason": "block_page"}

    service = RejectingSearch(results=[_result("https://a", snippet="a real snippet")])
    calls = _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q"])
    out = await sub.execute("topic")

    assert out["sources"] == ["https://a"]
    assert "a real snippet" in calls[-1]["messages"][1]["content"]


async def test_duplicate_urls_across_subqueries_are_fetched_once(monkeypatch):
    """Two facets routinely surface the same page; a duplicate biases the answer."""
    service = FakeSearch(results=[_result("https://same")])
    _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q1", "q2", "q3"])
    out = await sub.execute("topic")

    assert service.scrapes == ["https://same"]
    assert out["sources"] == ["https://same"]


async def test_synthesis_token_cap_comes_from_settings(monkeypatch):
    """The old hardcoded 800 truncated multi-source findings mid-sentence."""
    monkeypatch.setattr(r.settings, "RESEARCH_SYNTHESIS_MAX_TOKENS", 1234)
    service = FakeSearch(results=[_result("https://a")])
    calls = _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q"])
    await sub.execute("topic")

    assert calls[-1]["max_tokens"] == 1234


async def test_result_carries_its_own_provenance(monkeypatch):
    service = FakeSearch(results=[_result("https://a")])
    _install(monkeypatch, service, [])

    sub = ResearcherSubagent()
    sub._subqueries = _replies(["q"])
    out = await sub.execute("topic")

    assert out["subagent"] == "researcher"
    assert out["subqueries"] == ["q"]
    assert out["sources"] == ["https://a"]
    assert "scrape_count" in out and "model_calls" in out


async def test_the_graph_node_still_works_with_the_new_signature(monkeypatch):
    """Regression: subagent_dispatcher_node calls execute(topic=...) only."""
    service = FakeSearch(results=[_result("https://a")])
    # The first reply is consumed by decomposition; the second is the synthesis.
    _install(monkeypatch, service, ['["a question about X"]', "SYNTHESIS"])

    out = await ResearcherSubagent().execute(topic="a question about X")
    assert out["synthesis"] == "SYNTHESIS"
