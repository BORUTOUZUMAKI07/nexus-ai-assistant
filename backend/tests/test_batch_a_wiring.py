"""
Tests for Batch A — the four subsystems the streaming chat path bypassed.

Each test here fails if the corresponding wiring is reverted:
  * ``test_budget_history_*``        fail if the token-budget split is removed
  * ``test_summarize_*``             fail if summarisation stops being fail-open
  * ``test_synthesizer_*``           fail if the synthesizer reverts to the
                                     turn-count slice (nodes.py ``_MAX_HISTORY_TURNS``)
  * ``test_bind_request_context_*``  fail if version binding is removed
  * ``test_stream_route_*``          fail if the compiled prompt / skills are
                                     dropped from the graph input
"""
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from uuid import uuid4

import structlog
from backend.app.core.logging import bind_request_context, clear_request_context
from backend.app.services.context_compiler import ContextCompiler

# ── Fakes ────────────────────────────────────────────────────────────────────


class FakeMessage:
    """Minimal LangChain-BaseMessage stand-in (has ``.type`` and ``.content``)."""

    def __init__(self, content: str, msg_type: str = "human") -> None:
        self.content = content
        self.type = msg_type


class FakeClient:
    """Stands in for ``ai_client``; counts tokens 1 per 4 chars."""

    def __init__(self, summary: str = "SUMMARY", fail: bool = False) -> None:
        self.summary = summary
        self.fail = fail
        self.calls: list[dict] = []

    def count_tokens(self, text: str, model: str = "") -> int:
        return max(1, len(str(text)) // 4)

    async def completion(self, **kwargs):
        self.calls.append(kwargs)
        if self.fail:
            raise RuntimeError("provider exploded")
        return self.summary


def _compiler(client: FakeClient, ratio: float = 0.75, reserve: int = 4096) -> ContextCompiler:
    comp = ContextCompiler(target_budget_ratio=ratio, reserve_completion_tokens=reserve)
    comp.ai_client = client  # type: ignore[assignment]
    return comp


# ── _agent_message_parts normalisation ───────────────────────────────────────


def test_agent_message_parts_normalises_both_shapes():
    parts = ContextCompiler._agent_message_parts
    assert parts({"role": "assistant", "content": "hi"}) == ("assistant", "hi")
    assert parts(FakeMessage("yo", "human")) == ("user", "yo")
    assert parts(FakeMessage("yo", "ai")) == ("assistant", "yo")
    # Unknown/absent type must not raise and must not become a bogus role.
    assert parts(FakeMessage("z", "tool")) == ("user", "z")
    # A message with no content at all must not blow up token accounting.
    assert parts(FakeMessage(None))[1] == ""


# ── budget_history ───────────────────────────────────────────────────────────


def test_budget_history_keeps_newest_and_reports_overflow():
    client = FakeClient()
    comp = _compiler(client)
    # 200 messages of ~400 chars = ~20k tokens against a 128k*0.75-4096 budget,
    # so force a tiny budget to make the split observable.
    comp.get_max_context = lambda model: 400  # type: ignore[method-assign]
    history = [FakeMessage(f"message number {i} " + "x" * 400) for i in range(20)]

    keep, compress, used = comp.budget_history(history, system_prompt="sys")

    assert compress, "a 20x400-token history must overflow a 400-token window"
    assert keep, "the newest turns must always survive"
    assert used > 0
    # Everything is accounted for exactly once, order preserved.
    assert len(keep) + len(compress) == len(history)
    assert keep == history[-len(keep):]
    assert compress == history[: len(compress)]


def test_budget_history_respects_min_recent_turns_even_when_over_budget():
    comp = _compiler(FakeClient())
    comp.get_max_context = lambda model: 10  # type: ignore[method-assign]
    history = [FakeMessage("a" * 4000), FakeMessage("b" * 4000), FakeMessage("c" * 4000)]

    keep, compress, _ = comp.budget_history(history, system_prompt="sys", min_recent_turns=2)

    # The budget is impossible to satisfy, but the immediate exchange must survive.
    assert len(keep) == 2
    assert keep[-1].content.startswith("c")
    assert len(compress) == 1


def test_budget_history_under_budget_keeps_everything():
    comp = _compiler(FakeClient())
    history = [FakeMessage("short"), FakeMessage("also short")]
    keep, compress, _ = comp.budget_history(history, system_prompt="sys")
    assert compress == []
    assert keep == history


def test_budget_history_empty_input():
    comp = _compiler(FakeClient())
    keep, compress, used = comp.budget_history([], system_prompt="sys")
    assert (keep, compress, used) == ([], [], 0)


# ── summarize_agent_history (fail-open) ──────────────────────────────────────


async def test_summarize_agent_history_returns_summary():
    client = FakeClient(summary="  dense recap  ")
    comp = _compiler(client)
    out = await comp.summarize_agent_history(
        [FakeMessage("hello", "human"), FakeMessage("hi", "ai")]
    )
    assert out == "dense recap"
    assert client.calls, "summarisation must actually call the model"


async def test_summarize_agent_history_fails_open_on_provider_error():
    """A summarisation failure must return "" — never raise into the hot path."""
    comp = _compiler(FakeClient(fail=True))
    out = await comp.summarize_agent_history([FakeMessage("hello")])
    assert out == ""


async def test_summarize_agent_history_empty_is_noop():
    client = FakeClient()
    comp = _compiler(client)
    assert await comp.summarize_agent_history([]) == ""
    assert client.calls == [], "no history must mean no provider call"


# ── version binding (A4) ─────────────────────────────────────────────────────


def test_bind_request_context_injects_versions_and_clears():
    bind_request_context(conversation_id="conv-1", mode="research")
    try:
        bound = structlog.contextvars.get_contextvars()
        assert bound["conversation_id"] == "conv-1"
        assert bound["mode"] == "research"
        assert "agent_version" in bound
        assert "prompt_version" in bound
    finally:
        clear_request_context()
    assert structlog.contextvars.get_contextvars() == {}


def test_bind_request_context_drops_none_values():
    bind_request_context(conversation_id="c", mode=None)
    try:
        bound = structlog.contextvars.get_contextvars()
        assert "mode" not in bound, "None must be dropped, not logged as null"
    finally:
        clear_request_context()


def test_clear_request_context_is_safe_to_call_twice():
    bind_request_context(conversation_id="c")
    clear_request_context()
    clear_request_context()  # must not raise
    assert structlog.contextvars.get_contextvars() == {}


# ── load_active_skills (A3) ──────────────────────────────────────────────────


async def test_load_active_skills_fails_open(monkeypatch):
    """A database failure must degrade to "no skills", never fail the chat turn."""
    from backend.app.services import prompt_service

    class Boom:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory", lambda: Boom()
    )
    assert await prompt_service.load_active_skills() == []


async def test_load_active_skills_returns_rows(monkeypatch):
    from backend.app.domain.prompt.models import Skill
    from backend.app.services import prompt_service

    class FakeRepo:
        def __init__(self, session):
            pass

        async def get_skills(self, enabled_only=True):
            assert enabled_only is True, "only enabled skills may be injected"
            return [Skill(name="researcher", description="d", instructions="do research")]

    class Ok:
        async def __aenter__(self):
            return object()

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(
        "backend.app.infrastructure.database.session.async_session_factory", lambda: Ok()
    )
    monkeypatch.setattr(prompt_service, "PromptRepository", FakeRepo)
    skills = await prompt_service.load_active_skills()
    assert [s.name for s in skills] == ["researcher"]


# ── synthesizer uses the token budget (A1) ───────────────────────────────────


def _synth_state(history):
    return {
        "messages": history,
        "user_id": "",
        "conversation_id": "c1",
        "trace_id": "",
        "mode": "normal",
        "system_prompt": "SYSTEM",
        "task_type": "general",
        "revision_count": 0,
        "citations": [],
    }


async def test_synthesizer_compacts_history_by_token_budget(monkeypatch):
    """Reverting to the `_MAX_HISTORY_TURNS` slice fails this: a history that is
    short in turns but huge in tokens must be compacted, not replayed whole."""
    from backend.app.agents.orchestrator import nodes

    seen: dict = {}

    async def fake_completion(**kwargs):
        seen["messages"] = kwargs["messages"]
        return "DRAFT"

    async def fake_summarize(msgs, model=""):
        return "EARLIER CONTEXT RECAP"

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(nodes.context_compiler, "get_max_context", lambda model: 500)
    monkeypatch.setattr(nodes.context_compiler, "summarize_agent_history", fake_summarize)

    big = FakeMessage("x" * 6000, "human")
    await nodes.synthesizer_node(_synth_state([big] * 8))

    sent = seen["messages"]
    # The synthesizer drops the newest message (it is the live turn) then keeps
    # only what fits: 8 in -> 7 considered -> a couple replayed, not all 7.
    replayed = [m for m in sent if isinstance(m, dict) and m.get("role") != "system"]
    assert len(replayed) < 8, "oversized history must not be replayed in full"
    assert any(
        "Earlier conversation summary" in str(m.get("content", ""))
        for m in sent
        if isinstance(m, dict) and m.get("role") == "system"
    ), "overflow must be replaced by a summary block"


async def test_synthesizer_still_answers_when_summarization_fails(monkeypatch):
    """A summariser outage must not lose the user's turn."""
    from backend.app.agents.orchestrator import nodes

    async def fake_completion(**kwargs):
        return "DRAFT"

    async def boom(*a, **k):
        raise RuntimeError("summariser down")

    monkeypatch.setattr(nodes.ai_client, "completion", fake_completion)
    monkeypatch.setattr(nodes, "critic_subagent", _NoCritic())
    monkeypatch.setattr(nodes.context_compiler, "get_max_context", lambda model: 500)
    monkeypatch.setattr(nodes.context_compiler, "summarize_agent_history", boom)

    out = await nodes.synthesizer_node(_synth_state([FakeMessage("y" * 6000, "human")] * 8))
    assert out["messages"][0].content == "DRAFT"


class _NoCritic:
    async def evaluate(self, **kwargs):
        return {"approved": True, "critique": ""}


# ── stream route feeds the compiled prompt into the graph (A2/A3) ────────────


async def test_stream_route_passes_compiled_prompt_and_skills(monkeypatch):
    """The regression: the route used to send only {"messages", "mode"}, so the
    graph always fell back to its hardcoded prompt and never saw a skill."""
    from backend.app.api.deps import get_conversation_service, get_current_user
    from backend.app.api.v1 import conversations as conv_mod
    from backend.app.domain.user.models import User
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    captured: dict = {}

    class FakeGraph:
        def astream_events(self, input, config=None, version=None):
            captured.update(input)
            return _aiter([{"event": "on_chain_end", "data": {"output": {"messages": []}}}])

        async def aget_state(self, config):
            return None

    class FakePromptCompiler:
        async def compile_system_prompt_cached(self, **kwargs):
            captured["compile_kwargs"] = kwargs
            return "COMPILED PROMPT"

    class FakeSkill:
        name = "researcher"

    async def fake_skills():
        return [FakeSkill()]

    class FakeConvSvc:
        async def get_conversation(self, conv_id, user_id=None):
            class C:
                system_prompt = "custom conv prompt"
            return C()

        async def add_message(self, **kwargs):
            return None

    user = User(id=uuid4(), email="a@b.c", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_conversation_service] = lambda: FakeConvSvc()
    monkeypatch.setattr(conv_mod, "orchestrator_graph", FakeGraph())
    monkeypatch.setattr(conv_mod, "prompt_compiler", FakePromptCompiler())
    monkeypatch.setattr(conv_mod, "load_active_skills", fake_skills)
    monkeypatch.setattr(conv_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={"messages": [{"role": "user", "content": "hi"}], "mode": "normal"},
            )
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_conversation_service, None)

    assert captured.get("system_prompt") == "COMPILED PROMPT"
    assert captured.get("active_skills") == ["researcher"]
    assert captured["compile_kwargs"]["custom_instructions"] == "custom conv prompt"


async def test_stream_route_survives_prompt_compile_failure(monkeypatch):
    """Fail-open: a compiler outage must not break streaming chat."""
    from backend.app.api.deps import get_conversation_service, get_current_user
    from backend.app.api.v1 import conversations as conv_mod
    from backend.app.domain.user.models import User
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    captured: dict = {}

    class FakeGraph:
        def astream_events(self, input, config=None, version=None):
            captured.update(input)
            return _aiter([{"event": "on_chain_end", "data": {"output": {"messages": []}}}])

        async def aget_state(self, config):
            return None

    class Boom:
        async def compile_system_prompt_cached(self, **kwargs):
            raise RuntimeError("compiler down")

    class FakeConvSvc:
        async def get_conversation(self, conv_id, user_id=None):
            class C:
                system_prompt = None
            return C()

        async def add_message(self, **kwargs):
            return None

    user = User(id=uuid4(), email="a@b.c", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_conversation_service] = lambda: FakeConvSvc()
    monkeypatch.setattr(conv_mod, "orchestrator_graph", FakeGraph())
    monkeypatch.setattr(conv_mod, "prompt_compiler", Boom())
    monkeypatch.setattr(conv_mod, "load_active_skills", lambda: _raise_async())
    monkeypatch.setattr(conv_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={"messages": [{"role": "user", "content": "hi"}], "mode": "normal"},
            )
        assert resp.status_code == 200, resp.text
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_conversation_service, None)

    assert "system_prompt" not in captured, "must omit the key, not send a broken one"


# ── helpers ──────────────────────────────────────────────────────────────────


async def _raise_async():
    raise RuntimeError("skills unavailable")


class _NullOrg:
    def __init__(self, *a, **k):
        pass


@asynccontextmanager
async def _null_span(*a, **k) -> AsyncGenerator[None, None]:
    yield


async def _aiter(items) -> AsyncGenerator[dict, None]:
    for item in items:
        yield item

