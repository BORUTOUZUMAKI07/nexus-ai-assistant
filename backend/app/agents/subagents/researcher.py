"""
Researcher Subagent.

Runs a bounded search -> reflect -> synthesize loop over web evidence.

What changed and why
--------------------

The previous implementation was one search, one scrape pass, one synthesis
call. That is a fixed pipeline: it cannot notice that its own evidence was
inadequate, and it cannot look anywhere other than the top results of one
query. For a question with three parts, it would answer whichever part the
top-ranked snippet happened to cover.

Three additions, each bounded by an explicit ceiling because every one of them
costs a model call or a network round trip:

1. **Decomposition (breadth).** A cheap structured call splits the topic into
   up to ``RESEARCH_MAX_SUBQUERIES`` independent sub-queries, which are then
   searched *concurrently*. Multi-part questions stop depending on whichever
   facet the search engine happened to rank first.

2. **Reflection (depth).** After harvesting, the researcher judges its own
   evidence against the original topic and names what is missing. A bounded
   number of gap-filling queries are issued. This is the part the old code
   could not do at all: it had no way to notice thin evidence, because it
   never looked.

3. **Bounded parallelism and a total scrape budget.** Scrapes run
   concurrently up to ``RESEARCH_SCRAPE_CONCURRENCY``, but the *total* is
   capped by ``RESEARCH_MAX_SCRAPES`` across every sub-query, so breadth
   cannot multiply into an unbounded number of provider calls.

Cost discipline, in priority order:

* **The common case must not pay.** Decomposition and reflection are both
  skipped when they would add nothing: a single-facet topic, or a harvest
  already above ``RESEARCH_EVIDENCE_FLOOR_CHARS`` with no reflection budget
  left. Most turns therefore still cost one synthesis call, as before.
* **Fail-open throughout.** Every stage degrades to the next-best available
  behaviour. A failed decomposition falls back to searching the raw topic; a
  failed reflection is skipped; a failed search contributes nothing. The
  synthesis always runs, because an answer built from one search is far more
  useful than no answer.
* **Hard model-call ceiling.** ``RESEARCH_MAX_MODEL_CALLS`` bounds the whole
  turn, and each optional stage checks that its call still fits *before*
  making it, so the cap can never be spent into an over-budget call.
"""
from __future__ import annotations

import asyncio
import json
import re
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

DECOMPOSE_SYSTEM_PROMPT = """You split research questions into independent search queries.
Return ONLY a JSON array of strings. No prose, no numbering, no markdown fence.
Rules:
- Between 1 and 4 queries.
- Each must be independently searchable on its own.
- Together they must cover every part of the question.
- If the question is a single fact lookup, return exactly one query: the question itself.
Example for "Compare Postgres and MySQL for time-series data":
["PostgreSQL time series extensions TimescaleDB", "MySQL time series performance benchmarks"]"""

REFLECT_SYSTEM_PROMPT = """You assess whether retrieved sources actually answer a question.
Return ONLY a JSON object with exactly these keys:
{"sufficient": true|false, "gaps": ["...", "..."], "reason": "one short sentence"}
Rules:
- "sufficient" is true only if the sources cover every part of the question.
- "gaps" must be specific missing facts or facets, phrased as search queries.
  Empty list when sufficient.
- Judge only coverage, not quality. Do not ask for "better" sources."""

#: Chars kept per source. Enough for a few paragraphs of prose; the point is
#: evidence, not archival, and every source is competing for the same context.
SOURCE_CHARS = 4000

#: Below this a harvest is treated as a failure to find anything, and the
#: snippet-only fallback is used instead of skipping the source entirely.
SNIPPET_FALLBACK_CHARS = 600

#: Structured-output calls are cheap but not free; keep them small. The
#: decomposition and reflection replies are short by construction.
STRUCTURED_MAX_TOKENS = 200

#: Numbers are stripped before a sub-query or gap is used as a search query,
#: so a malformed model reply cannot smuggle anything odd into the provider.
_UNSAFE_QUERY_CHARS = re.compile(r"[^\w\s\-'\".,:;()\[\]/&+#%]")


def _clean_query(text: str, limit: int = 200) -> str:
    """Normalise a model-produced query into something safe to send."""
    cleaned = _UNSAFE_QUERY_CHARS.sub(" ", str(text or ""))
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned[:limit]


def _parse_json_list(raw: str) -> list[str]:
    """Pull a list of strings out of a model reply, tolerating prose and fences."""
    if not raw:
        return []
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("["), text.rfind("]")
    if start != -1 and end > start:
        try:
            parsed = json.loads(text[start : end + 1])
        except (ValueError, TypeError):
            return []
        if isinstance(parsed, list):
            return [str(x) for x in parsed if isinstance(x, (str, int, float))]
    return []


def _parse_reflection(raw: str) -> dict[str, Any]:
    """Pull the reflection object out of a model reply, defaulting to sufficient."""
    empty = {"sufficient": True, "gaps": [], "reason": "unparseable"}
    if not raw:
        return empty
    text = raw.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return empty
    try:
        parsed = json.loads(text[start : end + 1])
    except (ValueError, TypeError):
        return empty
    if not isinstance(parsed, dict):
        return empty
    gaps = parsed.get("gaps")
    return {
        "sufficient": bool(parsed.get("sufficient", True)),
        "gaps": [str(g) for g in gaps if isinstance(g, (str, int, float))] if isinstance(gaps, list) else [],
        "reason": str(parsed.get("reason", ""))[:200],
    }


class ResearcherSubagent:
    """
    Subagent that runs targeted research queries and formats findings.
    """

    def __init__(self) -> None:
        # Ceilings are read once per turn rather than per call so a single turn
        # cannot observe a settings change halfway through and end up with an
        # inconsistent budget.
        self.max_model_calls = max(1, settings.RESEARCH_MAX_MODEL_CALLS)
        self.calls_used = 0

    # -- model-call budget ---------------------------------------------------

    def _can_spend(self, calls_wanted: int = 1) -> bool:
        """Whether another `calls_wanted` calls still fit the turn ceiling.

        Checked *before* each optional call, not after, so the cap can never be
        exceeded by one extra round-trip.
        """
        return self.calls_used + calls_wanted <= self.max_model_calls

    async def _ask(self, system: str, user: str, max_tokens: int) -> str:
        """One budgeted, fail-open model call. Returns "" on any failure."""
        self.calls_used += 1
        try:
            return await ai_client.completion(
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                model=settings.FAST_MODEL,
                temperature=0.0,
                max_tokens=max_tokens,
            )
        except Exception as exc:
            # An outage in a cheap structured call must not cost the user the
            # actual answer, which is the synthesis below.
            logger.warning("researcher_model_call_failed", error_type=type(exc).__name__)
            return ""

    # -- stage 1: breadth ---------------------------------------------------

    async def _subqueries(self, topic: str) -> list[str]:
        """Decompose the topic, or return [topic] when decomposition is off.

        Decomposition is skipped when it cannot pay: a disabled budget, an
        already-spent call ceiling, or a topic too short to have facets.
        """
        if settings.RESEARCH_MAX_SUBQUERIES <= 1:
            return [topic]
        # One call must remain for the synthesis, which is the only mandatory
        # model call in the turn.
        if not self._can_spend(2):
            logger.info("researcher_decomposition_skipped_budget", calls_used=self.calls_used)
            return [topic]

        raw = await self._ask(
            DECOMPOSE_SYSTEM_PROMPT,
            f"Question: {topic}",
            STRUCTURED_MAX_TOKENS,
        )
        parsed = [_clean_query(q) for q in _parse_json_list(raw)]
        parsed = [q for q in parsed if q]

        # Guard against a degenerate decomposition: returning nothing, or
        # echoing the original question plus near-duplicates, is pure cost.
        if not parsed:
            return [topic]
        deduped = list(dict.fromkeys(parsed))[: settings.RESEARCH_MAX_SUBQUERIES]
        if len(deduped) == 1 and deduped[0].strip().lower() == topic.strip().lower():
            return [topic]
        logger.info("researcher_decomposed", subqueries=len(deduped))
        return deduped

    # -- stage 2: gather ----------------------------------------------------

    async def _harvest(
        self, queries: list[str], per_query: int, scrape_budget: int, seen: set[str]
    ) -> tuple[list[str], list[str], int]:
        """Search every sub-query concurrently, then fetch what needs fetching.

        Returns (evidence_blocks, source_urls, scrapes_actually_spent).

        Failures are per-sub-query, never fatal: one provider failure must not
        discard the evidence the others gathered.
        """
        results_per_query = await asyncio.gather(
            *(self._search_one(q, per_query) for q in queries),
            return_exceptions=True,
        )

        all_results: list[dict[str, Any]] = []
        for outcome in results_per_query:
            if isinstance(outcome, BaseException):
                logger.warning("researcher_search_failed", error_type=type(outcome).__name__)
                continue
            all_results.extend(outcome)

        # Deduplicate across sub-queries. Two facets of one question routinely
        # surface the same page, and a duplicated source biases the synthesis
        # toward one viewpoint while wasting the scrape budget.
        unique: list[dict[str, Any]] = []
        for res in all_results:
            url = (res.get("url") or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            unique.append(res)

        evidence, sources, spent = await self._gather_evidence(unique, scrape_budget)
        return evidence, sources, spent

    async def _search_one(self, query: str, per_query: int) -> list[dict[str, Any]]:
        """One search, returning [] on any failure."""
        try:
            return await web_search_service.search(query, max_results=max(per_query, 1))
        except Exception as exc:
            logger.warning(
                "researcher_search_failed", error_type=type(exc).__name__, query_length=len(query)
            )
            return []

    async def _gather_evidence(
        self, results: list[dict[str, Any]], scrape_budget: int
    ) -> tuple[list[str], list[str], int]:
        """Fetch full text for previews, concurrently and within budget.

        `requires_scraping` is declared by each provider on the result itself,
        so whether a fetch is needed is data rather than a guess from string
        length. Results that already carry a full body cost nothing.

        The snippet fallback matters as much as the skip: `scrape_url` rejects
        block pages and unextracted PDFs, so a "successful" scrape is not
        guaranteed. Without this, the content-shape guard would have *removed*
        evidence rather than filtering bad evidence out of it.
        """
        needs_fetch = [r for r in results if needs_scraping(r) and r.get("url")]

        allowed = needs_fetch[: max(0, scrape_budget)]
        if len(allowed) < len(needs_fetch):
            logger.info(
                "researcher_scrape_budget_exhausted",
                wanted=len(needs_fetch),
                allowed=len(allowed),
            )

        # Bound real concurrency, not just the total. Unbounded gather() would
        # open one connection per result at once, which is a good way to get
        # rate-limited by the provider and have every scrape fail together.
        limit = max(1, min(settings.RESEARCH_SCRAPE_CONCURRENCY, len(allowed) or 1))
        semaphore = asyncio.Semaphore(limit)

        async def _bounded(url: str) -> dict[str, Any]:
            async with semaphore:
                return await self._scrape_one(url)

        fetch_outcomes = await asyncio.gather(
            *(_bounded(r["url"]) for r in allowed),
            return_exceptions=True,
        )
        fetched: dict[str, dict[str, Any]] = {}
        spent = 0
        for res, outcome in zip(allowed, fetch_outcomes, strict=False):
            spent += 1
            if isinstance(outcome, BaseException):
                logger.warning("researcher_scrape_failed", error_type=type(outcome).__name__)
                continue
            fetched[res["url"]] = outcome

        evidence: list[str] = []
        sources: list[str] = []
        for res in results:
            url = res.get("url")
            if not url:
                continue
            snippet = res.get("content") or res.get("snippet") or ""
            scrape = fetched.get(url)
            if scrape and scrape.get("success") and scrape.get("content"):
                body = str(scrape["content"])[:SOURCE_CHARS]
            elif needs_scraping(res) and url not in fetched:
                # Rejected or budgeted out: keep whatever the search gave us.
                body = str(snippet)[:SNIPPET_FALLBACK_CHARS] if snippet else ""
            else:
                body = str(snippet)[:SOURCE_CHARS]

            if not body:
                continue
            sources.append(url)
            suffix = "snippet only" if len(body) <= SNIPPET_FALLBACK_CHARS else ""
            label = f"Source ({url}{', ' + suffix if suffix else ''})"
            evidence.append(f"{label}:\n{body}")

        return evidence, sources, spent

    async def _scrape_one(self, url: str) -> dict[str, Any]:
        try:
            return await web_search_service.scrape_url(url)
        except Exception as exc:
            logger.warning("researcher_scrape_failed", error_type=type(exc).__name__)
            return {"success": False, "rejection_reason": type(exc).__name__}

    # -- stage 3: depth (reflection) ----------------------------------------

    async def _reflect(
        self, topic: str, queries: list[str], evidence: list[str], scrape_budget: int
    ) -> tuple[list[str], list[str], list[str], int, dict[str, Any]]:
        """Inspect the harvested evidence and decide whether to look further.

        Returns (evidence, sources, follow_up_queries, scrapes_spent,
        reflection). Follow-up queries are returned rather than issued so the
        caller keeps control of the total budget.
        """
        if settings.RESEARCH_MAX_REFLECTIONS <= 0 or not self._can_spend(1):
            return evidence, [], [], 0, {"sufficient": True, "gaps": [], "reason": "budget"}

        total_chars = sum(len(e) for e in evidence)
        if total_chars >= settings.RESEARCH_EVIDENCE_FLOOR_CHARS:
            # A substantial harvest is usually good enough, and reflecting on
            # it only adds cost. Skipping here is what keeps the common case
            # at one model call.
            logger.info(
                "researcher_reflection_skipped_sufficient_evidence",
                evidence_chars=total_chars,
                floor=settings.RESEARCH_EVIDENCE_FLOOR_CHARS,
            )
            return evidence, [], [], 0, {"sufficient": True, "gaps": [], "reason": "evidence_sufficient"}

        raw = await self._ask(
            REFLECT_SYSTEM_PROMPT,
            f"Question: {topic}\n\nSub-queries already searched: {queries}\n\n"
            f"Retrieved sources:\n{self._preview_for_prompt(evidence)}",
            STRUCTURED_MAX_TOKENS,
        )
        reflection = _parse_reflection(raw)
        if reflection["sufficient"] or not reflection["gaps"]:
            logger.info("researcher_reflection_sufficient", reason=reflection["reason"])
            return evidence, [], [], 0, reflection

        # Reflection costs one call; the follow-up harvest costs none, so only
        # the synthesis needs to fit afterwards.
        if not self._can_spend(1):
            logger.info("researcher_gap_fill_skipped_budget", calls_used=self.calls_used)
            return evidence, [], [], 0, reflection

        follow_ups = [_clean_query(g) for g in reflection["gaps"]]
        follow_ups = [g for g in follow_ups if g][: settings.RESEARCH_MAX_SUBQUERIES]
        if not follow_ups:
            return evidence, [], [], 0, reflection

        logger.info("researcher_gap_fill", gaps=len(follow_ups), reason=reflection["reason"])
        new_evidence, new_sources, spent = await self._harvest(
            follow_ups, per_query=2, scrape_budget=scrape_budget, seen=set()
        )
        return evidence + new_evidence, new_sources, [], spent, reflection

    @staticmethod
    def _preview_for_prompt(evidence: list[str], per_source: int = 400) -> str:
        """A trimmed view of the evidence for the reflection call.

        Reflection judges coverage, which needs the gist of each source, not
        its full text. Passing everything would make the cheapest call in the
        turn the most expensive one.
        """
        if not evidence:
            return "(no sources retrieved)"
        return "\n\n---\n\n".join(block[: per_source + 120] for block in evidence)

    # -- entry point ---------------------------------------------------------

    async def execute(self, topic: str, max_sources: int = 3) -> dict[str, Any]:
        """Research `topic` and return a synthesis with its sources.

        `max_sources` is retained as the per-sub-query result count for
        backwards compatibility with the single-query behaviour it replaces.
        """
        logger.info("researcher_subagent_starting", topic_length=len(topic))
        self.calls_used = 0

        # 1. Breadth. Skipped when it cannot pay (see _subqueries).
        subqueries = await self._subqueries(topic)

        # 2. Gather. The scrape budget is total, not per sub-query, so breadth
        #    cannot multiply into unbounded provider calls.
        seen: set[str] = set()
        evidence, sources, spent = await self._harvest(
            subqueries, per_query=max_sources, scrape_budget=settings.RESEARCH_MAX_SCRAPES, seen=seen
        )

        # 3. Depth. Only runs when the harvest looks inadequate.
        extra_sources: list[str] = []
        reflection: dict[str, Any] = {"sufficient": True, "gaps": [], "reason": "not_attempted"}
        if evidence:
            evidence, extra_sources, _f, more_spent, reflection = await self._reflect(
                topic, subqueries, evidence, settings.RESEARCH_MAX_SCRAPES - spent
            )
            spent += more_spent
        sources = sources + [s for s in extra_sources if s not in sources]

        # 4. Synthesize. Mandatory: the answer is the point of the turn.
        context = "\n\n---\n\n".join(evidence) if evidence else "(no sources could be retrieved)"
        synthesis_prompt = (
            f"Synthesize findings on the following query based on these retrieved sources:\n\n"
            f"Topic: {topic}\n\nSources:\n{context}"
        )
        response = await self._ask(
            RESEARCHER_SYSTEM_PROMPT, synthesis_prompt, settings.RESEARCH_SYNTHESIS_MAX_TOKENS
        )

        logger.info(
            "researcher_completed",
            subqueries=len(subqueries),
            sources=len(sources),
            scrapes=spent,
            model_calls=self.calls_used,
            evidence_chars=sum(len(e) for e in evidence),
            reflected=reflection.get("reason", ""),
        )

        return {
            "subagent": "researcher",
            "topic": topic,
            "synthesis": response,
            "sources": sources,
            "subqueries": subqueries,
            "scrape_count": spent,
            "model_calls": self.calls_used,
            "reflection_reason": reflection.get("reason", ""),
            "gaps": list(reflection.get("gaps") or []),
        }


researcher_subagent = ResearcherSubagent()
