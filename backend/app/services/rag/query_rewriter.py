"""
Query Rewriting & Conditional HyDE Service.
Expands a single user query into 2-3 retrieval variants and conditionally
generates a hypothetical document (HyDE) for short / abstract / natural
language queries.

HyDE is skipped for exact keyword / code lookups, where it would inject
noise instead of precision.

What "hypothetical document" means here, because the difference is the whole
value: the model is asked to *imagine the answer*, and the imagined answer is
what gets embedded and searched. A question and its answer usually share little
wording -- "how do I stop the model looping" versus "add a per-run step ceiling
and a token budget" -- so the question alone retrieves documents that are about
the topic but never contain the answer. The imagined answer carries the answer's
vocabulary, which is what closes that gap.

The previous implementation returned a fixed template, "An overview of <query>,
including definition, key concepts, workflows, implementations, and practical
details." That contains no domain vocabulary at all, so it embeds close to the
original query -- which is already variant #1 -- and spends a full hybrid search
re-finding chunks that get deduplicated a moment later. It was the shape of HyDE
without the mechanism. It is kept as the `template` mode and as the fail-open
fallback, because "cheap, no model call, and definitely returns text" is the
right answer when generation is unavailable.
"""
import asyncio
import re
from typing import cast

import structlog
from backend.app.core.config import settings
from backend.app.services.rag.base import IRewriter

logger = structlog.get_logger(__name__)

MAX_VARIANTS = 3

# A hypothetical answer is a paragraph, not an essay. Past this it is being
# truncated mid-sentence anyway, and the extra tokens are charged per call.
MAX_HYDE_CHARS = 600

_CODE_TOKENS = {
    "code", "function", "def", "import", "class", "return", "api", "endpoint",
    "json", "http", "sql", "query", "regex", "cli", "flag",
}
_CODE_PATTERN = re.compile(r"[{}();=\[\]<>`]|\\[\\/]|\.\w{1,5}(?=\s|$)")
_QUESTION_WORDS = {"what", "how", "why", "which", "explain", "define", "when", "where", "who"}
_PATH_PATTERN = re.compile(r"[\\/][A-Za-z0-9_.-]+|(?:src|lib|app|test)/")

# Asking for a hypothetical *answer* rather than a summary. Two constraints are
# deliberate: the model must not mention that it is hypothetical (those words
# would then be searched for), and it must not hedge or ask to see sources
# (same problem, plus it makes the passage useless as evidence-shaped text).
_HYDE_SYSTEM_PROMPT = (
    "You write short technical reference passages for a search index. "
    "Given a question, write the answer as if it were an excerpt from a "
    "reference manual: two to four sentences of direct, specific, "
    "domain-specific prose. Do not mention that the passage is hypothetical or "
    "invented. Do not ask questions. Do not cite sources or hedge."
)


class QueryRewriterService(IRewriter):
    """
    Deterministic query expansion + conditional HyDE generation.

    The lexical half stays deterministic and LLM-free -- it runs on every query,
    including the code lookups where a model call would be wasted, and its
    determinism is what the expansion tests pin. Only the hypothesis itself is a
    model call, and only when `should_generate_hyde` has already decided the
    query is abstract enough to benefit.

    The model client is injected rather than imported so the class stays
    testable without network access, and so `None` is a meaningful value: it
    means "no model available", which degrades to the template instead of
    failing. The production singleton below passes the real client.
    """

    def __init__(
        self,
        llm: object | None = None,
        *,
        mode: str | None = None,
        model: str | None = None,
        max_tokens: int | None = None,
        timeout_seconds: float | None = None,
    ) -> None:
        self._llm = llm
        self._mode = (mode or settings.RAG_HYDE_MODE or "template").strip().lower()
        self._model = model or settings.RAG_HYDE_MODEL
        self._max_tokens = max_tokens if max_tokens is not None else settings.RAG_HYDE_MAX_TOKENS
        self._timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.RAG_HYDE_TIMEOUT_SECONDS
        )

    async def rewrite(self, query: str) -> list[str]:
        """Return 2-4 retrieval variants for the user query, HyDE included if earned."""
        variants = self.generate_multi_queries(query)
        hyde_included = False
        if self._mode != "off" and self.should_generate_hyde(query):
            hypothesis = await self.generate_hyde(query)
            if hypothesis:
                variants.append(hypothesis)
                hyde_included = True
            variants = variants[: MAX_VARIANTS + 1]
        logger.info(
            "query_rewritten",
            query=query,
            variant_count=len(variants),
            mode=self._mode,
            hyde_included=hyde_included,
        )
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

        seen: set[str] = set()
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

        Kept pure and LLM-free on purpose: it is the decision about whether to
        spend a model call, so it must be inspectable without one.
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

    async def generate_hyde(self, query: str) -> str:
        """
        Produce the hypothetical answer document, degrading to the template.

        Fail-open by design and in that direction specifically: a retrieval query
        must not fail because generating a hypothesis failed. Every failure path
        -- no client, timeout, provider error, empty or unusable text -- returns
        the template, so the worst case is the old behaviour and never an
        exception and never a missing variant.

        The failure is logged with a reason rather than swallowed, because a
        silently-always-template HyDE looks identical to a working one from the
        outside and would quietly keep costing a full search per query.
        """
        if self._mode == "template" or self._llm is None:
            if self._mode != "template":
                logger.info("hyde_llm_unavailable_using_template", mode=self._mode)
            return self._template_hyde(query)

        messages = [
            {"role": "system", "content": _HYDE_SYSTEM_PROMPT},
            {"role": "user", "content": f"Question: {query.strip()}\nPassage:"},
        ]

        try:
            raw = await asyncio.wait_for(
                self._call_llm(messages),
                timeout=self._timeout,
            )
        except asyncio.TimeoutError:
            logger.warning("hyde_llm_timeout_using_template", timeout_s=self._timeout)
            return self._template_hyde(query)
        except asyncio.CancelledError:
            # The request itself was cancelled -- a client disconnect or a
            # shutdown. Swallowing this would defeat cancellation, so it
            # propagates and no text is returned.
            raise
        except Exception as exc:
            logger.warning(
                "hyde_llm_failed_using_template",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            return self._template_hyde(query)

        document = self._clean(raw)
        if not document:
            logger.warning("hyde_llm_returned_unusable_text_using_template")
            return self._template_hyde(query)

        logger.info("hyde_generated", mode="llm", model=self._model, chars=len(document))
        return document

    async def _call_llm(self, messages: list[dict[str, str]]) -> str:
        """Single narrow call to the injected client. Never raises to the caller.

        `cast` rather than a `str()` coercion, deliberately: coercing here would
        turn a `None` reply into the four-character string "None", which
        `_clean` would then accept as a valid passage and embed. Validation
        belongs to `_clean`, which returns "" for anything that is not a usable
        string so the caller can fall back.
        """
        completion = getattr(self._llm, "completion", None)
        if completion is None:
            raise TypeError("injected llm client exposes no completion()")
        return cast(str, await completion(
            messages=messages,
            model=self._model,
            temperature=0.3,
            max_tokens=self._max_tokens,
        ))

    def _clean(self, raw: object) -> str:
        """Normalise model output to a single searchable paragraph.

        Strips the framing the prompt asks the model to avoid anyway ("Passage:",
        quotes, markdown fences) because a model that ignores the instruction
        should not have its scaffolding indexed and retrieved later.
        """
        if not isinstance(raw, str):
            return ""
        text = raw.strip()
        text = re.sub(r"^passage\s*:\s*", "", text, flags=re.IGNORECASE)
        text = text.strip().strip("`").strip()
        if text.startswith(('"', "'")) and text.endswith(('"', "'")) and len(text) > 1:
            text = text[1:-1].strip()
        text = " ".join(text.split())
        if not text:
            return ""
        if len(text) > MAX_HYDE_CHARS:
            text = text[:MAX_HYDE_CHARS].rsplit(" ", 1)[0]
        return text

    @staticmethod
    def _template_hyde(query: str) -> str:
        """
        The original fixed template: no model call, no domain vocabulary.

        Retained as the `template` mode and as the fail-open fallback. It is a
        worse query than the generated hypothesis -- close to a duplicate of the
        original query -- but it is never wrong and never costs anything, which
        is the right trade when generation is unavailable.
        """
        return (
            f"An overview of {query.strip()}, including definition, key concepts, "
            "workflows, implementations, and practical details."
        )


# Singleton instance for wiring consistency.
#
# The real client is passed here, and that line is the difference between HyDE
# working and HyDE silently running in template mode forever. Constructing
# `QueryRewriterService()` directly yields template behaviour, which is what
# makes the rest of this module testable without a network -- so an unwired
# singleton would look fine to every test except the one that checks this.
#
# Imported lazily inside a function because `litellm_client` pulls in the router
# and its cost map, which several services import at module scope; a top-level
# import here would make this module's import order significant. The resolved
# client is cached so the cost is paid once per process, not per query.
_ai_client: object | None = None
_ai_client_resolved = False


def _get_ai_client() -> object | None:
    """The process-wide LLM client, or None if it cannot be imported.

    Returning None rather than raising is deliberate and is the same
    fail-open contract `generate_hyde` uses everywhere else: a retrieval path
    must degrade to the template, not fail, because a client is missing.
    """
    global _ai_client, _ai_client_resolved
    if not _ai_client_resolved:
        _ai_client_resolved = True
        try:
            from backend.app.infrastructure.ai.litellm_client import ai_client

            _ai_client = ai_client
        except Exception as exc:  # pragma: no cover - import-time environment fault
            logger.warning(
                "hyde_ai_client_import_failed",
                error_type=type(exc).__name__,
                error=str(exc)[:200],
            )
            _ai_client = None
    return _ai_client


query_rewriter_service = QueryRewriterService(_get_ai_client())
