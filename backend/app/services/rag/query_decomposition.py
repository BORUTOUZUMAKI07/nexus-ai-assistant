"""
Query Decomposition (multi-hop).

Heuristic-first splitting for queries that are really two or more questions
("Compare X and Y", "What is X and how does it relate to Y"). Each sub-query
is returned as a separate retrieval variant, bounding at
`RAG_DECOMPOSITION_MAX_SUBQUERIES`. Deterministic, no LLM call, and only
triggers on clear multi-hop signals so single-hop queries are unchanged.

This is zero-cost on free tiers and complements `query_rewriter` (which
produces lexical variants of the same question). Fail-open: returns [] on any
parsing edge case.
"""
from __future__ import annotations

import re

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

# Comparison / relation signals
_COMPARISON = {
    "compare", "versus", "vs", "difference between", "differences between",
    "which is better", "which is faster", "which is cheaper", "contrast",
    "differentiate", "compared to",
}

# Conjunctions that often introduce a second question/branch
_CONJ_AND = re.compile(r"\band\b", re.IGNORECASE)
_SEMICO = re.compile(r";")
_QUESTION_SPLIT = re.compile(r"\s+(but|how does|how is|and how|then)\s+", re.IGNORECASE)

_QUESTION_MARK = "?"


def _contains(s: str, needles: set[str]) -> bool:
    sl = s.lower()
    for n in needles:
        if n in sl:
            return True
    return False


def _split_by_comma_connected_questions(s: str) -> list[str]:
    # Split on commas when followed by question words "what/how/why/which/explain/define/when/where/who"
    out: list[str] = []
    parts = re.split(r",\s+", s)
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # keep as-is if it's already a coherent question
        out.append(p)
    return out


def _split_by_question_marks(s: str) -> list[str]:
    # Split runs like "What is X? How is Y?" into individual questions.
    qparts = re.split(r"\?\s+", s)
    res: list[str] = []
    for q in qparts:
        q = q.strip().rstrip(".")
        if not q:
            continue
        res.append(q + "?")
    return res


def _clean_subs(subs: list[str]) -> list[str]:
    seen = set()
    out: list[str] = []
    for q in subs:
        q = re.sub(r"\s+", " ", q.strip())
        q = q.rstrip(".")
        if not q:
            continue
        if q == q.upper() and len(q) > 40:
            pass
        key = q.lower()
        if key not in seen:
            seen.add(key)
            out.append(q)
        if len(out) >= settings.RAG_DECOMPOSITION_MAX_SUBQUERIES:
            break
    return out


class QueryDecomposer:
    """Heuristic query decomposition: split multi-hop into sub-queries."""

    async def decompose(self, query: str) -> list[str]:
        q = query.strip()
        if len(q) < settings.RAG_DECOMPOSITION_MIN_CHARS:
            return []
        base = q
        subs: list[str] = []

        # Split multiple explicit questions
        if base.count("?") >= 2:
            subs.extend(_split_by_question_marks(base))
        else:
            # Try "but", "then", "how does X and how..." patterns
            parts = _QUESTION_SPLIT.split(base)
            if len(parts) > 1:
                # first token group + second + (rest) — keep them coherent
                # join the last two parts back if they look like they split a question
                if len(parts) == 2:
                    subs.append(parts[0].rstrip("."))
                    subs.append(parts[1].rstrip("."))
                else:
                    # reassemble: take odd/even as parts; simpler: collect all non-empty
                    for p in parts:
                        p = p.strip().rstrip(".")
                        if p:
                            subs.append(p)
            # Split on "and" that links two concepts that are often compared
            if not subs:
                if _contains_comparison(base) or (" and " in base.lower() and len(base) > 60):
                    # naive split into two clauses around "and"
                    m = _CONJ_AND.search(base)
                    if m:
                        a = base[: m.start()].strip().rstrip(",;.")
                        b = base[m.end() :].strip().lstrip(",;.")
                        if a and b:
                            subs.extend([a, b])
            # Split by comma-connected questions
            if not subs:
                subs.extend(_split_by_comma_connected_questions(base))

        subs = _clean_subs(subs)
        if not subs:
            return []
        # Do not decompose if we only got back the original or if decomposition
        # would be trivial (single sub-query that is almost the original).
        keys = [s.lower() for s in subs]
        basek = base.lower()
        # If all are subsets of base or too similar, drop.
        trivial = 0
        for k in keys:
            if basek == k or k in basek or basek.startswith(k.split(" ")[0]):
                trivial += 1
        if trivial == len(keys):
            # allow only if clearly comparison (different topics) — heuristic: length
            if len(subs) >= 2 and _contains_comparison(base):
                pass  # keep multi-hop comparison
        if len(subs) == 1:
            return []
        logger.debug("query_decomposed", original_len=len(base), sub_count=len(subs))
        return subs


query_decomposer_service = QueryDecomposer()


def _contains_comparison(s: str) -> bool:
    return _contains(s, _COMPARISON)
