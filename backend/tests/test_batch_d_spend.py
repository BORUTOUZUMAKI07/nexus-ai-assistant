"""
Tests for D2 - the per-run spend ceiling.

The ceiling only means something if it (a) actually refuses work, (b) counts
the model calls it is supposed to count, and (c) does not reset when a run
suspends for HITL. Each test fails if its fix is reverted.
"""

import pytest
from backend.app.core.spend import (
    DEFAULT_STEP_TOKEN_ESTIMATE,
    SpendMeter,
    bind_spend_meter,
    budget_exhausted,
    charge_step,
    charge_tokens,
    clear_spend_meter,
    current_spend_meter,
    reset_spend_meter,
)


@pytest.fixture(autouse=True)
def _no_leaked_meter():
    """Every test starts and ends with nothing bound.

    A leaked meter is exactly the failure this feature could introduce, so it
    must not be able to happen silently inside the suite either.
    """
    clear_spend_meter()
    yield
    clear_spend_meter()


# -- counting -----------------------------------------------------------------


def test_a_broken_meter_is_loud_not_silent():
    """Pins the absence-vs-breakage distinction.

    A missing meter must not disable the feature, so that case is silent. A
    meter that *raises* is a bug in the meter, and swallowing it would hide the
    bug from the test that introduced it -- while also leaving the run with no
    ceiling and no clue why.
    """
    class _Broken:
        def charge_tokens(self, _n):
            raise RuntimeError("meter is broken")

    token = bind_spend_meter(limit_tokens=100, limit_steps=1)
    try:
        current_spend_meter().__class__ = _Broken  # type: ignore[assignment]
        with pytest.raises(RuntimeError):
            charge_tokens(10)
    finally:
        reset_spend_meter(token)


def test_no_meter_means_no_ceiling():
    """Fails open: a budgeting bug must not disable the feature it budgets."""
    assert current_spend_meter() is None
    assert budget_exhausted() == (False, "")


def test_charges_accumulate():
    token = bind_spend_meter(limit_tokens=1000, limit_steps=10)
    try:
        charge_tokens(100)
        charge_tokens(250)
        assert current_spend_meter().tokens == 350
        assert current_spend_meter().calls == 2
    finally:
        reset_spend_meter(token)


def test_non_numeric_and_negative_charges_are_ignored():
    """Usage accounting must never be the thing that breaks a generation."""
    token = bind_spend_meter(limit_tokens=1000, limit_steps=10)
    try:
        charge_tokens("abc")
        charge_tokens(None)
        charge_tokens(-500)
        charge_tokens(0)
        assert current_spend_meter().tokens == 0
        assert current_spend_meter().calls == 0
    finally:
        reset_spend_meter(token)


def test_steps_accumulate_independently_of_tokens():
    token = bind_spend_meter(limit_tokens=1000, limit_steps=2)
    try:
        charge_step()
        charge_step()
        assert current_spend_meter().steps == 2
        assert current_spend_meter().steps_exhausted
        assert not current_spend_meter().tokens_exhausted
    finally:
        reset_spend_meter(token)


# -- the ceiling actually refuses ---------------------------------------------


def test_step_limit_stops_the_run():
    token = bind_spend_meter(limit_tokens=10_000, limit_steps=2)
    try:
        charge_step()
        charge_step()
        blocked, reason = budget_exhausted()
        assert blocked
        assert reason == "step_limit"
    finally:
        reset_spend_meter(token)


def test_token_budget_stops_the_run():
    token = bind_spend_meter(limit_tokens=500, limit_steps=100)
    try:
        charge_tokens(500)
        blocked, reason = budget_exhausted()
        assert blocked
        assert reason == "token_budget"
    finally:
        reset_spend_meter(token)


def test_the_check_happens_before_the_work_not_after():
    """A ceiling that only notices afterwards has already paid for the thing.

    With 100 tokens left and a step estimated at 2000, the step must be
    refused even though the meter is nowhere near its limit.
    """
    token = bind_spend_meter(limit_tokens=2100, limit_steps=100)
    try:
        charge_tokens(2000)
        assert not current_spend_meter().exhausted, "under the limit, so not yet"
        blocked, reason = budget_exhausted(estimated_tokens=2000)
        assert blocked, "a step that would overrun must be refused"
        assert reason == "token_budget"
    finally:
        reset_spend_meter(token)


def test_can_afford_is_true_with_headroom():
    token = bind_spend_meter(limit_tokens=10_000, limit_steps=10)
    try:
        charge_tokens(100)
        assert current_spend_meter().can_afford(1000)
    finally:
        reset_spend_meter(token)


def test_the_first_ceiling_to_bite_is_the_one_reported():
    token = bind_spend_meter(limit_tokens=100, limit_steps=1)
    try:
        charge_step()
        _blocked, first = budget_exhausted()
        _blocked2, second = budget_exhausted(estimated_tokens=9999)
        assert first == "step_limit"
        assert second == "step_limit", "a later check must not rewrite the cause"
        assert current_spend_meter().stop_reason == "step_limit"
    finally:
        reset_spend_meter(token)


def test_an_absurd_estimate_does_not_break_the_check():
    token = bind_spend_meter(limit_tokens=10_000, limit_steps=10)
    try:
        blocked, _reason = budget_exhausted(estimated_tokens="not a number")
        assert not blocked
    finally:
        reset_spend_meter(token)


def test_default_estimate_is_positive():
    assert DEFAULT_STEP_TOKEN_ESTIMATE > 0


# -- lifecycle ----------------------------------------------------------------


def test_reset_restores_no_meter():
    token = bind_spend_meter(limit_tokens=100, limit_steps=1)
    assert current_spend_meter() is not None
    reset_spend_meter(token)
    assert current_spend_meter() is None


def test_a_leaked_meter_would_silence_the_ceiling_for_the_next_request():
    """The failure this design has to rule out, stated as a test.

    Contextvars outlive the statement that set them. If a request forgets to
    clear its meter, the next request served by the same task inherits one that
    is already exhausted -- and an exhausted meter means the ceiling refuses
    nothing, which is the opposite of the intended behaviour and completely
    silent.
    """
    token = bind_spend_meter(limit_tokens=10, limit_steps=1)
    charge_step()  # this request exhausts itself
    assert budget_exhausted()[0] is True

    # Correct behaviour: clear, and the next run is unbounded again.
    reset_spend_meter(token)
    assert current_spend_meter() is None
    assert budget_exhausted() == (False, "")


def test_reset_from_a_foreign_context_drops_the_meter():
    """Resetting where the set did not happen must not leave a stale meter."""
    bind_spend_meter(limit_tokens=1, limit_steps=1)

    class _ForeignToken:
        pass

    reset_spend_meter(_ForeignToken())  # type: ignore[arg-type]
    assert current_spend_meter() is None


def test_clear_is_idempotent():
    clear_spend_meter()
    clear_spend_meter()
    assert current_spend_meter() is None


# -- durability across a HITL suspend -----------------------------------------


def test_a_resumed_run_inherits_the_spend_already_made():
    """The bug a contextvar-only design would have.

    A run that suspends for tool approval resumes in a *later request*, where
    the contextvar is gone. Without seeding from the checkpointed state the
    budget restarts at zero, so a run that keeps asking for approval can spend
    without limit -- which is precisely the shape of run a ceiling is for.
    """
    token = bind_spend_meter(
        limit_tokens=1000, limit_steps=10, seed_tokens=900, seed_steps=2
    )
    try:
        assert current_spend_meter().tokens == 900
        assert current_spend_meter().steps == 2
        charge_tokens(200)
        assert budget_exhausted()[0] is True
    finally:
        reset_spend_meter(token)


def test_seeding_never_lowers_a_higher_count():
    """A resume cannot be used to *lower* a count, only to restore one."""
    token = bind_spend_meter(limit_tokens=1000, limit_steps=10, seed_tokens=10)
    try:
        charge_tokens(500)
        current_spend_meter().merge_from(tokens=10, steps=0)
        assert current_spend_meter().tokens == 510, "the 500 spent this run was lost"
    finally:
        reset_spend_meter(token)


def test_seeding_with_a_higher_value_raises_the_count():
    token = bind_spend_meter(limit_tokens=1000, limit_steps=10)
    try:
        current_spend_meter().merge_from(tokens=400, steps=4)
        assert current_spend_meter().tokens == 400
        assert current_spend_meter().steps == 4
    finally:
        reset_spend_meter(token)


def test_merging_ignores_garbage():
    meter = SpendMeter()
    meter.merge_from(tokens="nope", steps=None)
    assert meter.tokens == 0 and meter.steps == 0


def test_snapshot_is_loggable_and_carries_no_content():
    meter = SpendMeter(limit_tokens=100, limit_steps=2)
    meter.charge_tokens(50)
    meter.charge_step()
    snap = meter.snapshot()
    assert snap["tokens"] == 50
    assert snap["steps"] == 1
    assert snap["tokens_remaining"] == 50
    assert snap["steps_remaining"] == 1
    assert snap["exhausted"] is False
    assert all(isinstance(v, (int, str, bool)) for v in snap.values())


def test_snapshot_serialises():
    import json

    meter = SpendMeter()
    meter.charge_tokens(10)
    json.dumps(meter.snapshot())  # must not raise


# -- wiring: the model client charges the meter -------------------------------


def test_the_model_client_charges_every_call(monkeypatch):
    """Automatic counting is the whole design. Prove it at the real call site."""
    from backend.app.infrastructure.ai import litellm_client as lc

    charged: list[int] = []
    monkeypatch.setattr(lc, "charge_tokens", charged.append)

    class _Usage:
        prompt_tokens = 120
        completion_tokens = 30

    class _Msg:
        content = "hi"

    class _Choice:
        message = _Msg()

    class _Response:
        model = "m"
        usage = _Usage()
        choices = [_Choice()]

    class _Router:
        async def acompletion(self, **kwargs):
            return _Response()

    async def _ok():
        return True

    async def _drive():
        client = lc.LiteLLMService.__new__(lc.LiteLLMService)
        client.router = _Router()
        client._breaker = type(
            "_B",
            (),
            {
                "allow": staticmethod(lambda *a: _ok()),
                "record_success": staticmethod(lambda *a: _ok()),
            },
        )()
        client._in_flight = _NoInflight()
        return await client.complete(messages=[{"role": "user", "content": "x"}])

    import asyncio

    asyncio.run(_drive())
    assert charged == [150], "the model's real token usage must reach the meter"


class _NoInflight:
    def acquire(self, _group):
        return self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


def test_streaming_charges_an_estimate_not_nothing(monkeypatch):
    """A stream that charges nothing would make the whole number a fiction.

    litellm only reports usage on a stream when the request opts in with
    stream_options, which is not set here, so the charge is a documented
    estimate. It still has to happen: the final answer is often the largest
    single item in a run.
    """
    from backend.app.infrastructure.ai import litellm_client as lc

    charged: list[int] = []
    monkeypatch.setattr(lc, "charge_tokens", charged.append)

    class _Delta:
        def __init__(self, content):
            self.content = content

    class _Choice:
        def __init__(self, content):
            self.delta = _Delta(content)

    class _Chunk:
        def __init__(self, content):
            self.choices = [_Choice(content)]

    class _Stream:
        model = "m"

        def __aiter__(self):
            return self._gen()

        async def _gen(self):
            for piece in ("a" * 40, "b" * 40, "c" * 40):
                yield _Chunk(piece)

        async def aclose(self):
            return None

    class _Router:
        async def acompletion(self, **kwargs):
            return _Stream()

    async def _ok():
        return True

    async def _drive():
        client = lc.LiteLLMService.__new__(lc.LiteLLMService)
        client.router = _Router()
        client._breaker = type(
            "_B",
            (),
            {
                "allow": staticmethod(lambda *a: _ok()),
                "record_success": staticmethod(lambda *a: _ok()),
            },
        )()
        client._in_flight = _NoInflight()
        out = []
        async for piece in client.astream(messages=[{"role": "user", "content": "x"}]):
            out.append(piece)
        return "".join(out)

    import asyncio

    text = asyncio.run(_drive())
    assert text == "a" * 40 + "b" * 40 + "c" * 40
    assert charged, "an abandoned stream must still be charged"
    assert charged[0] > 0


# -- wiring: the graph binds it and publishes it ------------------------------


@pytest.fixture
def _runnable(monkeypatch):
    """bootstrap_node reads LangGraph's run config, which needs a run context.

    Patched rather than wrapped in a real graph run: the behaviour under test
    is the meter binding, and standing up a checkpointer-backed run to observe
    it would test LangGraph instead.
    """
    from backend.app.agents.orchestrator import nodes

    monkeypatch.setattr(nodes, "get_config", lambda: {"configurable": {}})


async def test_bootstrap_binds_a_meter(_runnable):
    from backend.app.agents.orchestrator import nodes

    out = await nodes.bootstrap_node({"messages": []})
    assert current_spend_meter() is not None
    assert out["tokens_used"] == 0
    assert out["loop_steps"] == 0


async def test_bootstrap_seeds_from_resumed_state(_runnable):
    """The HITL-resume case, at the real node."""
    from backend.app.agents.orchestrator import nodes

    await nodes.bootstrap_node({"messages": [], "tokens_used": 50_000, "loop_steps": 3})
    meter = current_spend_meter()
    assert meter.tokens == 50_000
    assert meter.steps == 3


async def test_bootstrap_does_not_replace_an_already_bound_meter(_runnable):
    """Rebinding would discard this request's spend and raise the ceiling."""
    from backend.app.agents.orchestrator import nodes

    token = bind_spend_meter(limit_tokens=999, limit_steps=1)
    try:
        charge_tokens(400)
        await nodes.bootstrap_node({"messages": []})
        assert current_spend_meter().tokens == 400, "spend already made was discarded"
        assert current_spend_meter().limit_tokens == 999
    finally:
        reset_spend_meter(token)


async def test_bootstrap_survives_a_malformed_seed(_runnable):
    """A checkpoint written by an older build must not crash the run."""
    from backend.app.agents.orchestrator import nodes

    out = await nodes.bootstrap_node({"messages": [], "tokens_used": "not a number"})
    assert out["tokens_used"] == 0


async def test_spend_state_publishes_the_total(_runnable):
    from backend.app.agents.orchestrator import nodes

    await nodes.bootstrap_node({"messages": []})
    charge_tokens(123)
    charge_step()
    published = nodes._spend_state({})
    assert published["tokens_used"] == 123
    assert published["loop_steps"] == 1


def test_spend_state_is_empty_without_a_meter():
    """Zeroing a caller's counters would be worse than publishing nothing."""
    from backend.app.agents.orchestrator import nodes

    clear_spend_meter()
    assert nodes._spend_state({}) == {}


def test_state_declares_the_spend_fields():
    """A rename here would silently drop the counters on checkpoint."""
    from backend.app.agents.orchestrator.state import AgentState

    for key in ("loop_steps", "tokens_used", "spend_stop_reason"):
        assert key in AgentState.__annotations__


def test_config_ceilings_are_positive():
    from backend.app.core.config import settings

    assert settings.AGENT_LOOP_MAX_STEPS >= 1
    assert settings.AGENT_LOOP_TOKEN_BUDGET >= 1000


# -- wiring: the synthesizer's revision loop obeys the ceiling ----------------


class _Msg:
    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class _Critic:
    """A critic that never approves, so the revision loop wants to run forever."""

    def __init__(self) -> None:
        self.calls = 0

    async def evaluate(self, **kwargs):
        self.calls += 1
        return {"approved": False, "critique": f"issue {self.calls}"}


def _research_state():
    # task_type "research" is what makes CRITIC_MAX_REVISIONS apply at all.
    return {
        "messages": [_Msg("Explain the topic")],
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "research",
        "system_prompt": "SYSTEM",
        "task_type": "research",
        "revision_count": 0,
        "citations": [],
    }


async def _drive_synthesizer(monkeypatch, limit_steps: int, limit_tokens: int):
    """Run the synthesizer with a critic that always rejects.

    Returns (result_dict, critic_call_count, draft_call_count).
    """
    from backend.app.agents.orchestrator import nodes

    drafts: list[str] = []

    async def fake_completion(**kwargs):
        drafts.append(kwargs["messages"][-1]["content"])
        return f"draft number {len(drafts)}"

    critic = _Critic()
    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", critic)

    token = bind_spend_meter(limit_tokens=limit_tokens, limit_steps=limit_steps)
    try:
        out = await nodes.synthesizer_node(_research_state())
    finally:
        reset_spend_meter(token)
    return out, critic.calls, drafts


async def test_the_revision_loop_stops_when_the_step_budget_runs_out(monkeypatch):
    """The loop's own ceiling allows 3 passes. The run's budget allows 2.

    Without the budget check this is three draft+critique pairs; with it, two.
    """
    monkeypatch.setattr(nodes_settings(), "CRITIC_MAX_REVISIONS", 2)

    _out, critic_calls, drafts = await _drive_synthesizer(
        monkeypatch, limit_steps=2, limit_tokens=10_000_000
    )

    assert len(drafts) == 2, f"the budget did not stop the loop: {len(drafts)} drafts"
    assert critic_calls == 2


async def test_the_revision_loop_is_not_shortened_when_there_is_headroom(monkeypatch):
    """The counter-test: the budget must not be what makes the loop short.

    If the ceiling were checked with the wrong sign, or compared against the
    revision count instead of the run's spend, this test would still pass while
    the one above passed for the wrong reason.
    """
    monkeypatch.setattr(nodes_settings(), "CRITIC_MAX_REVISIONS", 2)

    _out, critic_calls, drafts = await _drive_synthesizer(
        monkeypatch, limit_steps=100, limit_tokens=10_000_000
    )

    assert len(drafts) == 3, "the run's own ceiling should have been reached"
    assert critic_calls == 3


async def test_a_budget_stop_still_returns_the_last_draft(monkeypatch):
    """Cutting the loop short must not cost the user their answer."""
    monkeypatch.setattr(nodes_settings(), "CRITIC_MAX_REVISIONS", 2)

    out, _critic_calls, drafts = await _drive_synthesizer(
        monkeypatch, limit_steps=1, limit_tokens=10_000_000
    )

    assert drafts, "no draft was produced to fall back on"
    text = out["messages"][0].content
    assert text, "the budget stop must not produce an empty answer"
    assert drafts[-1][:40] in text or "draft" in text


async def test_the_token_budget_also_stops_the_loop(monkeypatch):
    """Two independent ceilings; both must be honoured."""
    monkeypatch.setattr(nodes_settings(), "CRITIC_MAX_REVISIONS", 5)

    _out, _critic_calls, drafts = await _drive_synthesizer(
        monkeypatch, limit_steps=100, limit_tokens=1
    )

    assert len(drafts) < 6, "the token ceiling did not apply"


async def test_the_synthesizer_checkpoints_the_runs_total(monkeypatch):
    """The total has to leave the node, or a resume restarts the budget."""
    from backend.app.agents.orchestrator import nodes

    async def fake_completion(**kwargs):
        return "an answer"

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _Critic())
    monkeypatch.setattr(nodes_settings(), "CRITIC_MAX_REVISIONS", 0)

    token = bind_spend_meter(limit_tokens=10_000_000, limit_steps=100)
    try:
        charge_tokens(4321)
        charge_step()
        out = await nodes.synthesizer_node(_research_state())
    finally:
        reset_spend_meter(token)

    assert out["tokens_used"] == 4321, "the run's total was not published"
    assert out["loop_steps"] >= 1


def nodes_settings():
    from backend.app.agents.orchestrator import nodes

    return nodes.settings
