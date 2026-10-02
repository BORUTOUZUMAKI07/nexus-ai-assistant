"""The HyDE hypothesis must actually be a hypothesis.

The bug this file exists for: `generate_hyde` returned a fixed template --
"An overview of <query>, including definition, key concepts, workflows,
implementations, and practical details." -- which is the *shape* of HyDE with
none of the mechanism. That string contains no domain vocabulary, so it embeds
close to the original query, which is already variant #1, and the fourth
retrieval pass re-finds chunks that get deduplicated immediately afterwards.

So the tests below are written against the failure that matters: a
"generate_hyde returns something non-empty" assertion passes just as well on the
template. Every test here distinguishes the two, and asserts the direction of
each failure path as well -- because a fail-open implementation that returns
`""` instead of the template would look healthy to a
`assert result` and quietly drop a query variant from every abstract search.

The client is injected, so none of this touches the network.
"""
from __future__ import annotations

import asyncio

import pytest
from backend.app.services.rag.query_rewriter import (
    MAX_HYDE_CHARS,
    QueryRewriterService,
    query_rewriter_service,
)

# The old template, reproduced as a marker. Any test that wants to prove the LLM
# was actually used must assert its ABSENCE, and that only works if the literal
# is here rather than imported from the module under test -- importing it would
# mean a change to the template silently changed the detector too.
TEMPLATE_MARKER = "overview of"

ANSWER_TEXT = (
    "Give the run a per-step ceiling and a cumulative token budget; when either "
    "is exhausted the synthesizer stops revising and returns the last draft."
)


class _RecordingLLM:
    """Stands in for `ai_client`, recording exactly what it was asked."""

    def __init__(self, reply=ANSWER_TEXT, *, error=None, delay=0.0):
        self.reply = reply
        self.error = error
        self.delay = delay
        self.calls: list[dict] = []

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.reply


def _rewriter(llm, **kwargs) -> QueryRewriterService:
    kwargs.setdefault("mode", "llm")
    return QueryRewriterService(llm, **kwargs)


# ── the headline: the hypothesis comes from the model ────────────────────────


@pytest.mark.asyncio
async def test_the_hypothesis_is_the_models_answer_not_the_template():
    """The whole change. A non-empty result is not enough -- it must not be the
    template, because the template was the bug."""
    llm = _RecordingLLM()
    rewriter = _rewriter(llm)

    result = await rewriter.generate_hyde("How does RAG work?")

    assert result == ANSWER_TEXT
    assert TEMPLATE_MARKER not in result.lower(), (
        "the old template came back; this is the defect being fixed"
    )


@pytest.mark.asyncio
async def test_the_hypothesis_reaches_the_variant_list():
    """Not only generated, but actually handed to retrieval.

    This is the wiring trap: a `generate_hyde` that works while `rewrite`
    discards its result would pass every test above. Asserted on the returned
    list, in last position, so both the inclusion and the ordering are pinned.
    """
    llm = _RecordingLLM()
    variants = await _rewriter(llm).rewrite("How does RAG work?")

    assert ANSWER_TEXT in variants, "the hypothesis never reached the search"
    assert variants[-1] == ANSWER_TEXT, "the hypothesis must be the final variant"
    assert len(variants) <= 4, f"variant budget blown: {variants}"
    assert variants[0] == "How does RAG work?", "the fidelity anchor must stay first"


@pytest.mark.asyncio
async def test_it_asks_for_an_answer_not_a_summary():
    """A summary of the question is what the template already was.

    Pinned on the request rather than the reply, because the reply is the model's
    business -- what is ours is asking for a passage with domain vocabulary
    instead of a paraphrase of the question.
    """
    llm = _RecordingLLM()
    await _rewriter(llm).generate_hyde("How does RAG work?")

    assert len(llm.calls) == 1
    messages = llm.calls[0]["messages"]
    system = " ".join(m["content"] for m in messages if m["role"] == "system").lower()
    assert "answer" in system, "the prompt must ask for the answer"
    assert "hypothetical" not in system or "do not mention" in system


@pytest.mark.asyncio
async def test_the_call_goes_through_the_charged_path():
    """`.completion()`, not the router or `complete()`.

    `LiteLLMService.complete` is the single place that calls `charge_tokens`
    (litellm_client.py:453), chosen at implementation time precisely so that no
    call site has to remember to. Reaching past it for the raw router, or for
    `acompletion`, would produce a HyDE call that costs money and is invisible
    to the per-run spend ceiling in `core/spend.py` -- the exact leak the
    centralised charge exists to prevent.
    """
    llm = _RecordingLLM()
    await _rewriter(llm).generate_hyde("How does RAG work?")

    used = [k for k in llm.calls[0]]
    assert "messages" in used and "model" in used
    assert "temperature" in used and "max_tokens" in used


@pytest.mark.asyncio
async def test_the_generation_budget_is_a_cheap_model_with_a_ceiling():
    """Free-tier-first is a constraint, not a preference.

    One short hypothetical per abstract query, on the query path, in front of
    every user. An unbounded `max_tokens` here would put the most expensive call
    in the system on the hottest path.
    """
    llm = _RecordingLLM()
    await _rewriter(llm, max_tokens=180).generate_hyde("How does RAG work?")

    call = llm.calls[0]
    assert call["max_tokens"] == 180, "the caller's ceiling must reach the client"
    assert 0 < call["temperature"] <= 0.5, (
        "high temperature makes the hypothesis vary run to run for no benefit"
    )


@pytest.mark.asyncio
async def test_the_configured_ceiling_is_small_by_default():
    """Guards the settings default rather than the plumbing.

    A test that passes `max_tokens=180` proves nothing about what production
    sends; this reads the real default.
    """
    llm = _RecordingLLM()
    rewriter = _rewriter(llm, max_tokens=None)
    await rewriter.generate_hyde("How does RAG work?")

    assert llm.calls[0]["max_tokens"] <= 512, (
        "RAG_HYDE_MAX_TOKENS has grown past a short-passage budget"
    )


# ── when NOT to spend a call ──────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "query",
    [
        "import FastAPI from fastapi",
        "DELETE FROM users WHERE id = 5",
        "src/app/main.py",
        "rm -rf /tmp/cache",
    ],
)
async def test_code_lookups_spend_nothing(query):
    """The gate is the cost control; bypassing it is spending on nothing."""
    llm = _RecordingLLM()
    await _rewriter(llm).rewrite(query)

    assert llm.calls == [], f"a model call was made for a code lookup: {query}"


@pytest.mark.asyncio
async def test_off_mode_makes_no_call_and_adds_no_variant():
    llm = _RecordingLLM()
    variants = await QueryRewriterService(llm, mode="off").rewrite("How does RAG work?")

    assert llm.calls == []
    assert all(v != ANSWER_TEXT for v in variants)
    assert len(variants) <= 3, f"off mode still expanded: {variants}"


@pytest.mark.asyncio
async def test_template_mode_makes_no_call_and_uses_the_template():
    """Kept so the old behaviour stays reachable and comparable.

    This is the arm you flip to measure whether the LLM call earns its cost.
    """
    llm = _RecordingLLM()
    rewriter = QueryRewriterService(llm, mode="template")

    result = await rewriter.generate_hyde("How does RAG work?")

    assert llm.calls == []
    assert TEMPLATE_MARKER in result.lower()


@pytest.mark.asyncio
async def test_an_unwired_service_degrades_to_the_template_instead_of_failing():
    """`QueryRewriterService()` with no client -- what every test gets by default.

    If this raised, the module would be untestable without a network. If it
    returned `""`, every abstract search would silently lose a variant.
    """
    result = await QueryRewriterService().generate_hyde("How does RAG work?")
    assert TEMPLATE_MARKER in result.lower()


@pytest.mark.asyncio
async def test_the_production_singleton_is_actually_wired_to_a_client():
    """The unwired-singleton check.

    Everything above constructs its own service, so a `query_rewriter_service`
    built without a client would pass every one of them -- while production ran
    the template forever with no error anywhere. This is the failure mode that
    a class of test structurally cannot catch, so it gets its own.
    """
    assert query_rewriter_service._llm is not None, (
        "the module singleton has no LLM client; production would silently use "
        "the template. See _get_ai_client()."
    )
    assert query_rewriter_service._mode == "llm", (
        f"production HyDE mode is {query_rewriter_service._mode!r}, expected 'llm'"
    )


# ── fail-open: every failure returns the template, never an exception ──────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "label"),
    [
        (RuntimeError("provider down"), "provider error"),
        (TimeoutError("upstream timeout"), "timeout raised by the client"),
        (ValueError("malformed"), "client contract violation"),
    ],
)
async def test_a_failed_call_degrades_to_the_template(error, label):
    llm = _RecordingLLM(error=error)

    result = await _rewriter(llm).generate_hyde("How does RAG work?")

    assert TEMPLATE_MARKER in result.lower(), f"{label} did not degrade"
    assert result, f"{label} returned an empty variant"


@pytest.mark.asyncio
async def test_a_slow_call_is_cut_off_rather_than_awaited_forever():
    """Retrieval must not wait on generation.

    The ceiling is small because this runs in front of every user query; without
    it a slow provider turns a fast keyword search into a request that hangs for
    the provider's timeout instead of falling back.
    """
    llm = _RecordingLLM(delay=5.0)
    rewriter = _rewriter(llm, timeout_seconds=0.05)

    result = await rewriter.generate_hyde("How does RAG work?")

    assert TEMPLATE_MARKER in result.lower()
    assert result != ANSWER_TEXT


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   ",
        None,
        12345,
        ["a", "list"],
    ],
)
async def test_unusable_model_output_degrades_to_the_template(reply):
    """`complete()` documents content as "never None" (litellm_client.py:467), but
    that is the happy path of one router; anything at all can arrive here."""
    llm = _RecordingLLM(reply=reply)

    result = await _rewriter(llm).generate_hyde("How does RAG work?")

    assert TEMPLATE_MARKER in result.lower()


@pytest.mark.asyncio
async def test_a_client_without_completion_degrades_rather_than_raising():
    """An object injected in place of the client must not turn into an AttributeError
    escaping into the query path."""

    class _NotAClient:
        pass

    result = await _rewriter(_NotAClient()).generate_hyde("How does RAG work?")
    assert TEMPLATE_MARKER in result.lower()


@pytest.mark.asyncio
async def test_cancellation_is_not_swallowed():
    """The one failure that must propagate.

    `asyncio.CancelledError` is how a client disconnect and a shutdown reach
    this code. Catching it and returning the template would defeat cancellation
    and leave work running after the request that wanted it stopped is gone --
    `generate_hyde` catches `Exception`, and `CancelledError` inherits from
    `BaseException` precisely so that broad handlers cannot eat it.
    """
    llm = _RecordingLLM(delay=5.0)
    rewriter = _rewriter(llm, timeout_seconds=30.0)

    task = asyncio.create_task(rewriter.generate_hyde("How does RAG work?"))
    await asyncio.sleep(0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


# ── the text that gets embedded ──────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("dirty", "must_be_absent", "why"),
    [
        ("Passage: Tokens refill every 500ms.", "Passage:", "the prompt's own label"),
        ("```\nTokens refill every 500ms.\n```", "```", "a fenced code block"),
        ('"Tokens refill every 500ms."', '"', "surrounding quotes"),
        ("Tokens  refill\r\nevery 500ms.", "  ", "collapsed whitespace"),
    ],
)
async def test_model_framing_is_stripped_before_it_is_indexed(dirty, must_be_absent, why):
    """A model that ignores the instruction should not have its scaffolding
    retrieved later.

    "Passage:" is a token that appears in no reference manual, and the Qdrant
    sparse vectors are BM25 over exactly these strings, so scaffolding is
    directly searchable noise.
    """
    llm = _RecordingLLM(reply=dirty)

    result = await _rewriter(llm).generate_hyde("How do rate limits work?")

    assert must_be_absent not in result, f"{why} survived into the indexed text"
    assert result == "Tokens refill every 500ms."


@pytest.mark.asyncio
async def test_an_overlong_hypothesis_is_truncated_at_a_word_boundary():
    """Truncation must cut between words, not inside one.

    The word length here is 6, so the 600-character cut at `MAX_HYDE_CHARS`
    lands 5 characters into a word rather than on a separator. That is the whole
    point of the fixture: with 5-letter words the cut lands exactly on the space
    at 600 and a naive `text[:MAX_HYDE_CHARS]` -- the obvious implementation --
    would still produce a last "word" that exists in the input, so the
    mid-word assertion would pass against the very bug it is written to catch.
    """
    reply = "abcdef " * 100
    assert len(reply) > MAX_HYDE_CHARS
    # Guard the fixture itself: the naive slice must be mid-word, or this test
    # proves nothing regardless of what the implementation does.
    assert reply[:MAX_HYDE_CHARS].split()[-1] == "abcde", (
        "fixture no longer exercises a mid-word cut"
    )

    llm = _RecordingLLM(reply=reply)
    result = await _rewriter(llm).generate_hyde("How does RAG work?")

    assert 0 < len(result) <= MAX_HYDE_CHARS
    assert result.split()[-1] == "abcdef", "truncated mid-word"
    assert "abcde" not in result.split(), "a partial token survived the cut"


@pytest.mark.asyncio
async def test_the_cleaned_text_is_what_reaches_retrieval():
    """Cleaning in isolation proves nothing; this proves the cleaned form is
    the one returned, and therefore the one embedded."""
    llm = _RecordingLLM(reply='Passage:  "The  answer is 42."  ')

    variants = await _rewriter(llm).rewrite("How does RAG work?")

    assert variants[-1] == "The answer is 42."
