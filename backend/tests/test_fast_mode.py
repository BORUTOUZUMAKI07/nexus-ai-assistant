"""Fast mode is a real execution shape, not a label on a toggle.

The composer's Fast/Deep toggle used to be cosmetic: `page.tsx` mapped it to
`normal`/`agent`, and the backend read neither, so both ran the identical graph.
Fast now means "a single direct synthesizer call" and Deep means "the full agent
graph". Two edits make that true, and each is separately necessary:

* `nodes.planner_node` returns before its planning LLM call when mode is fast.
* `graph.route_after_planner` sends fast straight to the synthesizer.

Asserting only one would miss the failure mode where the other half is reverted:
the early-return alone still hits the orchestrator's classifier call, and the
route change alone still spends a planning call on every fast turn. So both are
covered, plus a control proving the normal path is unchanged.

Run: `cd backend && uv run pytest tests/test_fast_mode.py`
"""
from __future__ import annotations

import pytest
from backend.app.agents.orchestrator import nodes
from backend.app.agents.orchestrator.graph import (
    _build_workflow,
    route_after_planner,
)
from langchain_core.messages import AIMessage, HumanMessage


def _state(mode: str, content: str = "hi") -> dict:
    return {"messages": [HumanMessage(content=content)], "mode": mode, "user_id": ""}


# A request that clears BOTH planner short-circuits: longer than 400 chars (the
# cheap pre-check) and carrying a step-trigger keyword, so the normal path is
# guaranteed to reach the planning LLM call rather than returning early.
_LONG_STEP_REQUEST = (
    "search the web for the latest news and then compare it with the attached "
    "document and summarize the differences. "
) * 8


def _assert_would_plan_normally() -> None:
    """Fixture precondition: the request is genuinely a planner-LLM case."""
    assert len(_LONG_STEP_REQUEST) > 400
    lowered = _LONG_STEP_REQUEST.lower()
    assert any(tok in lowered for tok in nodes._STEP_TRIGGER_KEYWORDS)


# ── Routing ──────────────────────────────────────────────────────────────────


def test_fast_mode_routes_straight_to_the_synthesizer():
    assert route_after_planner(_state("fast")) == "synthesizer"


@pytest.mark.parametrize("mode", ["normal", "agent", "code", "research"])
def test_every_other_mode_still_routes_through_the_orchestrator(mode):
    assert route_after_planner(_state(mode)) == "orchestrator"


def test_a_plan_still_takes_the_tree_of_thoughts_path():
    state = _state("normal")
    state["plan"] = ["step 1", "step 2"]
    assert route_after_planner(state) == "tree_of_thoughts"


def test_fast_mode_wins_over_a_plan():
    """Fast is checked before the plan, so it cannot be dragged back into ToT.

    Not reachable while `planner_node` also short-circuits fast mode, but the
    ordering is what makes the two edits agree instead of one masking the other.
    """
    state = _state("fast")
    state["plan"] = ["step 1"]
    assert route_after_planner(state) == "synthesizer"


def test_the_planner_branch_is_wired_to_the_synthesizer():
    """The §2 lesson: a correct routing function nothing calls is dead config.

    Reads the branch off the builder `add_conditional_edges` actually created,
    not the source text, so a future edit that drops the `synthesizer` entry
    from the map fails here even though the function still returns the string.
    """
    branch = _build_workflow().branches["planner"]["route_after_planner"]
    assert branch.ends["synthesizer"] == "synthesizer"


# ── The planner LLM call ─────────────────────────────────────────────────────


async def test_fast_mode_skips_the_planner_llm_call(monkeypatch):
    _assert_would_plan_normally()
    calls: list[dict] = []

    async def _completion(*args, **kwargs):
        calls.append(kwargs)
        return "1. Search\n2. Summarize"

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)

    result = await nodes.planner_node(_state("fast", _LONG_STEP_REQUEST))

    assert calls == [], "fast mode must not spend a planning call"
    assert result["plan"] is None


async def test_normal_mode_still_calls_the_planner(monkeypatch):
    """The control: the same request DOES plan outside fast mode.

    Without this, `test_fast_mode_skips_the_planner_llm_call` would pass for a
    request that never planned in the first place.
    """
    _assert_would_plan_normally()
    calls: list[dict] = []

    async def _completion(*args, **kwargs):
        calls.append(kwargs)
        return "1. Search\n2. Summarize"

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)

    result = await nodes.planner_node(_state("normal", _LONG_STEP_REQUEST))

    assert len(calls) == 1
    assert result["plan"] == ["1. Search", "2. Summarize"]


async def test_fast_mode_still_loads_long_term_memories(monkeypatch):
    """Fast drops the agent machinery, not personalisation.

    The memory block is loaded before the fast early-return on purpose; moving
    the return above it would silently strip memory from every fast turn.
    """

    async def _memory(*, query, user_id, limit):
        return "User prefers Python 3.12."

    monkeypatch.setattr(nodes.long_term_memory, "build_memory_context_block", _memory)

    async def _no_llm(*args, **kwargs):  # pragma: no cover - must never run
        raise AssertionError("fast mode must not call the planner LLM")

    monkeypatch.setattr(nodes.ai_client, "completion", _no_llm)

    state = _state("fast", "what version should I use")
    state["user_id"] = "u-1"
    result = await nodes.planner_node(state)

    assert result["user_memories"] == ["User prefers Python 3.12."]
    assert result["plan"] is None


# -- ToT feeds the orchestrator, not the synthesizer -------------------------


def test_a_planned_request_reaches_the_orchestrator_after_tree_of_thoughts():
    """A plan used to edge straight to the synthesizer, skipping everything.

    The planner decomposes a multi-step request into steps, ``route_after_planner``
    sends it to Tree of Thoughts, and ToT then went directly to the synthesizer --
    so the orchestrator, the subagents, the tool node and the retrieval/critic
    grader were all unreachable for exactly the requests that needed them. A
    "search the web, then summarise" prompt the planner decomposed never touched
    the web tool.

    Reading the *builder's* edges, not the function's return value: the route can
    still return ``tree_of_thoughts`` while the edge out of that node points
    somewhere else, which is the shape that shipped.
    """
    edges = {(e[0], e[1]) for e in _build_workflow().edges}

    assert ("tree_of_thoughts", "orchestrator") in edges
    assert ("tree_of_thoughts", "synthesizer") not in edges


# -- ToT informs the answer without becoming the question ---------------------


async def test_tree_of_thoughts_does_not_inject_an_assistant_turn(monkeypatch):
    """The node used to return ``{"messages": [AIMessage(...)]}``.

    That made the ToT synthesis the last message in the state, so every
    downstream reader of ``messages[-1]`` -- the orchestrator's router, the
    critic grader's retrieval query, the subagent dispatcher and the
    synthesizer's prompt -- operated on the ToT output instead of the user's
    turn. The result now travels in state.
    """
    from backend.app.agents.orchestrator import tot_node

    async def _completion(**kwargs):
        return "Approach A\n---APPROACH---\nApproach B"

    monkeypatch.setattr(tot_node.ai_client, "completion", _completion)

    out = await tot_node.tree_of_thoughts_node(_state("normal", "the real question"))

    assert "messages" not in out, "ToT must not inject a synthetic assistant turn"
    assert out["tot_synthesis"]
    assert out["tot_candidates"] == ["Approach A", "Approach B"]


def test_latest_user_content_reads_the_user_turn_not_a_trailing_tot_turn():
    """The reader that makes "the user's request" explicit.

    ``[HumanMessage(Q), AIMessage(ToT)]`` is exactly the state the old ToT node
    produced; the answer must be ``Q``, never the synthesis.
    """
    messages = [
        HumanMessage(content="what is the capital of France"),
        AIMessage(content="ToT synthesized best approach"),
    ]
    assert nodes._latest_user_content(messages) == "what is the capital of France"


def test_latest_user_content_falls_back_to_the_final_message():
    """Control: a transcript with no human marker keeps its previous meaning.

    Older checkpoints and hand-built fixtures may carry plain dicts, or no
    human turn at all; the helper must degrade to the old ``messages[-1]``.
    """
    assert nodes._latest_user_content([]) == ""
    assert nodes._latest_user_content([{"role": "assistant", "content": "only"}]) == "only"
    assert (
        nodes._latest_user_content(
            [{"role": "user", "content": "Q"}, {"role": "assistant", "content": "A"}]
        )
        == "Q"
    )


async def test_the_synthesizer_asks_the_user_question_when_tot_ran(monkeypatch):
    """End to end through the node: ToT is context, the user's turn is the prompt.

    Reverting the derivation (back to ``messages[-1]``) makes the captured prompt
    start with the ToT synthesis instead of the question.
    """
    from backend.app.services.confidence_service import confidence_service

    captured: list[dict] = []

    async def _completion(**kwargs):
        captured.append(kwargs)
        return "Paris."

    monkeypatch.setattr(nodes.ai_client, "completion", _completion)
    monkeypatch.setattr(
        confidence_service,
        "decide",
        lambda *a, **k: {
            "confidence": 0.9,
            "threshold": 0.6,
            "action": "answer",
            "grounded": True,
            "calibrated": False,
        },
    )

    state = {
        "messages": [
            HumanMessage(content="what is the capital of France"),
            AIMessage(content="ToT synthesized best approach"),
        ],
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
        "tot_synthesis": "Proposed approach: reason about capitals.",
    }
    await nodes.synthesizer_node(state)

    prompt = captured[-1]["messages"][-1]["content"]
    assert prompt.startswith("what is the capital of France")
    # The ToT synthesis still reaches the model -- as guidance, not as the ask.
    assert "Tree of Thoughts" in prompt
    assert "Proposed approach" in prompt

