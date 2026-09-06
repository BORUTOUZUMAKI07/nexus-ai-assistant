"""
Query Rewriting & Conditional HyDE Service.
Expands a single user query into 2-3 retrieval variants and conditionally
generates a hypothetical document (HyDE) for short / abstract / natural
language queries.

HyDE is skipped for exact keyword / code lookups, where it would inject
noise instead of precision.
"""
import re

import structlog
from backend.app.services.rag.base import IRewriter

logger = structlog.get_logger(__name__)

MAX_VARIANTS = 3

_CODE_TOKENS = {
    "code", "function", "def", "import", "class", "return", "api", "endpoint",
    "json", "http", "sql", "query", "regex", "cli", "flag",
}
_CODE_PATTERN = re.compile(r"[{}();=\[\]<>`]|\\[\\/]|\.\w{1,5}(?=\s|$)")
_QUESTION_WORDS = {"what", "how", "why", "which", "explain", "define", "when", "where", "who"}
_PATH_PATTERN = re.compile(r"[\\/][A-Za-z0-9_.-]+|(?:src|lib|app|test)/")


class QueryRewriterService(IRewriter):
    """
    Deterministic (LLM-free) query expansion + conditional HyDE generation.
    Keeps behaviour testable while remaining composable with an LLM rewriter
    behind the IRewriter interface later.
    """

    def rewrite(self, query: str) -> list[str]:
        """Return 2-3 retrieval variants for the user query."""
        variants = self.generate_multi_queries(query)
        if self.should_generate_hyde(query):
            variants.append(self.generate_hyde(query))
            variants = variants[: MAX_VARIANTS + 1]
        logger.info("query_rewritten", query=query, variants=variants, hyde_included=len(variants) > 3 or any("overview of" in v for v in variants))
        return variants

    def generate_multi_queries(self, query: str) -> list[str]:
        """
        Deterministic lexical expansions:
        1. Original query (fidelity anchor)
        2. Token-rotation variant (term-order robustness)
        3. Abstract phrasing variant ("what is ...")
        """
        base = query.strip()
        if not base:
            return []

        tokens = re.findall(r"\S+", base)
        variants: list[str] = [base]

        if len(tokens) > 1:
            variants.append(" ".join(tokens[1:] + tokens[:1]))

        if not any(t.lower() in _QUESTION_WORDS for t in tokens):
            variants.append(f"what is {base}")

        seen: set = set()
        out: list[str] = []
        for v in variants:
            v = v.strip()
            if v and v.lower() not in seen:
                seen.add(v.lower())
                out.append(v)
            if len(out) >= MAX_VARIANTS:
                break
        return out or [base]

    def should_generate_hyde(self, query: str) -> bool:
        """
        Conditional HyDE gate: generate a hypothesis only for short / abstract
        natural-language queries; skip exact keyword / code lookups.
        """
        q = query.strip()
        if len(q) < 3:
            return False
        tokens = re.findall(r"\w+", q)
        if not tokens:
            return False
        word_count = len(tokens)

        has_code = bool(
            _CODE_PATTERN.search(q)
            or any(t.lower() in _CODE_TOKENS for t in tokens)
            or any(t.startswith(("get", "post", "put", "delete")) for t in tokens)
        )
        has_path = bool(_PATH_PATTERN.search(q) or "/" in q or "\\" in q)

        # Exact keyword / code lookups never benefit from a hypothetical document
        if has_code or has_path:
            return False

        is_shouty = q.isupper()
        is_short = word_count <= 5
        is_abstract = word_count <= 12 and not is_shouty
        return is_short or is_abstract

    def generate_hyde(self, query: str) -> str:
        """
        Deterministic hypothetical-document template. In a deployed LLM branch,
        this can be swapped for `litellm.acompletion` behind the same interface.
        """
        return f"An overview of {query.strip()}, including definition, key concepts, workflows, implementations, and practical details."


# Singleton instance for wiring consistency
query_rewriter_service = QueryRewriterService()
