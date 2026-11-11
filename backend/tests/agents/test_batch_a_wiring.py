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
import asyncio
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
    # The model's prose comes first, then the deterministic state tail. The tail
    # exists because a prose summary drops exactly the facts a resumed turn
    # needs (the open question, artifacts, the last failure) -- see
    # `_state_tail` in context_compiler.
    assert out.startswith("dense recap")
    assert "[State carried over" in out
    assert "last user question: hello" in out
    assert client.calls, "summarisation must actually call the model"


async def test_summarize_agent_history_fails_open_on_provider_error():
    """A summarisation failure must never raise into the hot path.

    It used to return "" and that was the whole contract. It now returns the
    state tail instead: the tail is built from the messages, not from the
    provider, so a failed summary still carries the part a resumed turn needs,
    and dropping it would discard the only state that survived.
    """
    comp = _compiler(FakeClient(fail=True))
    out = await comp.summarize_agent_history([FakeMessage("hello")])
    assert "[State carried over" in out
    assert "last user question: hello" in out
    # Nothing was invented: the provider said nothing, so no prose appears.
    assert "dense recap" not in out


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
    from backend.app.services import run_executor as run_exec_mod
    from backend.tests.fakes import FakeRunSession, FakeRunStore
    from httpx import ASGITransport, AsyncClient

    captured: dict = {}

    class FakeGraph:
        def astream_events(self, input, config=None, version=None, stream_mode=None):
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
    # `trace_span` moved to the executor, which is where the work happens: the
    # span used to wrap the request, and with the run decoupled that would have
    # measured only the time the route spent tailing frames. Same name, same
    # attributes (`run_executor.py`), different owner -- so it is patched there
    # now. Patching the route would have raised AttributeError and taken both
    # tests with it, which is the only reason this surfaced at all.
    monkeypatch.setattr(run_exec_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)
    # A fake session for the run log, which the endpoint now owns.
    #
    # The route passes `conv_mod.async_session_factory` to the background
    # executor, the tail reader reads through it, and the reply persistence
    # opens its own session from it (project guidelines). Left unpatched, these two
    # tests wrote to whatever `DATABASE_URL` points at -- hosted Supabase in this
    # checkout -- and failed on `relation "agent_runs" does not exist`. That is a
    # fact about which migrations someone has applied to someone else's database,
    # not about the prompt-and-skills wiring these tests exist to check, and the
    # right response to it is emphatically not to migrate the remote DB.
    #
    # One store shared by every session: the writer, the reader and the finisher
    # each open their own, and a fresh store per call would give each an empty
    # world of its own.
    run_store = FakeRunStore()
    monkeypatch.setattr(
        conv_mod, "async_session_factory", lambda: FakeRunSession(run_store)
    )

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
    from backend.app.services import run_executor as run_exec_mod
    from backend.tests.fakes import FakeRunSession, FakeRunStore
    from httpx import ASGITransport, AsyncClient

    captured: dict = {}

    class FakeGraph:
        def astream_events(self, input, config=None, version=None, stream_mode=None):
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
    # See the note in the sibling test: the span moved to the executor with the
    # run, and kept its name and attributes.
    monkeypatch.setattr(run_exec_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)
    # A fake session for the run log, which the endpoint now owns.
    #
    # The route passes `conv_mod.async_session_factory` to the background
    # executor, the tail reader reads through it, and the reply persistence
    # opens its own session from it (project guidelines). Left unpatched, these two
    # tests wrote to whatever `DATABASE_URL` points at -- hosted Supabase in this
    # checkout -- and failed on `relation "agent_runs" does not exist`. That is a
    # fact about which migrations someone has applied to someone else's database,
    # not about the prompt-and-skills wiring these tests exist to check, and the
    # right response to it is emphatically not to migrate the remote DB.
    #
    # One store shared by every session: the writer, the reader and the finisher
    # each open their own, and a fresh store per call would give each an empty
    # world of its own.
    run_store = FakeRunStore()
    monkeypatch.setattr(
        conv_mod, "async_session_factory", lambda: FakeRunSession(run_store)
    )

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


# ── the route hands its own session factory to the background executor ───────


async def test_stream_route_runs_the_executor_through_its_own_session_factory(monkeypatch):
    """The fake session must be reached by the *executor*, not only by the reader.

    Found by `backend/revert_c2.py` revert B9. Dropping
    ``session_factory=async_session_factory`` from the ``execute_run`` call site
    left both sibling tests above green, which looks like "the argument is
    redundant" and is actually "nothing observes it": they assert on the graph
    invocation (``captured["system_prompt"]``) and return as soon as the HTTP
    response is in hand. The run is a *background* task, so it had not yet
    reached its first `writer.flush()` -- the first thing that opens a session --
    when the test ended and the loop tore the task down.

    So the seam that exists specifically so these tests cannot write to hosted
    Supabase (§9.27) was itself unverified. The next person to write a test that
    waits for the run would have discovered it by writing a row to someone
    else's database.

    The signal is the end of the response body, not a sleep: the endpoint's
    stream ends when the run reaches a terminal state, and the run can only
    reach one after `_terminate` has called ``finish_run`` on a session. If the
    executor used its import-time default instead, the fake store stays empty and
    this fails -- after reaching for the real database, which is precisely why
    the assertion is worth having.
    """
    from backend.app.api.deps import get_conversation_service, get_current_user
    from backend.app.api.v1 import conversations as conv_mod
    from backend.app.domain.user.models import User
    from backend.app.main import app
    from backend.app.services import run_executor as run_exec_mod
    from backend.tests.fakes import FakeRunSession, FakeRunStore
    from httpx import ASGITransport, AsyncClient

    class FakeGraph:
        def astream_events(self, input, config=None, version=None, stream_mode=None):
            return _aiter([{"event": "on_chain_end", "data": {"output": {"messages": []}}}])

        async def aget_state(self, config):
            return None

    class FakePromptCompiler:
        async def compile_system_prompt_cached(self, **kwargs):
            return "COMPILED PROMPT"

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
    monkeypatch.setattr(conv_mod, "prompt_compiler", FakePromptCompiler())
    monkeypatch.setattr(conv_mod, "load_active_skills", lambda: _aiter_ok())
    monkeypatch.setattr(run_exec_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)
    run_store = FakeRunStore()
    monkeypatch.setattr(
        conv_mod, "async_session_factory", lambda: FakeRunSession(run_store)
    )

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={"messages": [{"role": "user", "content": "hi"}], "mode": "normal"},
            )
            assert resp.status_code == 200, resp.text
            # Reading to EOF is what forces the run to finish: the route's stream
            # ends on the run's terminal status, not on its own timer.
            body = await asyncio.wait_for(resp.aread(), timeout=10.0)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_conversation_service, None)

    assert b"run_id" in body or resp.headers.get("X-Nexus-Run-Id"), (
        "the stream must announce the run it is tailing"
    )
    assert len(run_store.runs) == 1, (
        "the executor never opened the route's session factory"
    )
    assert run_store.runs[0].status in {"completed", "failed"}, (
        f"run never reached a terminal status: {run_store.runs[0].status!r}"
    )
    assert [e.seq for e in run_store.events] == sorted(e.seq for e in run_store.events)


async def test_stream_route_adopts_a_reused_user_turn_instead_of_appending(
    monkeypatch,
):
    """A resend must adopt the stored turn, not store the question twice.

    Regenerate and edit-and-resend both replay a prompt whose row already exists.
    The route persists the user turn itself on every stream request, so those
    paths used to append a second copy -- measured live as five rows where four
    were expected (project guidelines). The
    client sends the row id back as ``reuse_user_message_id``; this pins both
    halves of the branch, because "reuse works" and "a plain send still appends"
    are separately breakable.
    """
    from backend.app.api.deps import get_conversation_service, get_current_user
    from backend.app.api.v1 import conversations as conv_mod
    from backend.app.domain.user.models import User
    from backend.app.main import app
    from backend.app.services import run_executor as run_exec_mod
    from backend.tests.fakes import FakeRunSession, FakeRunStore
    from httpx import ASGITransport, AsyncClient

    calls: dict[str, list] = {"reused": [], "added": []}

    class FakeGraph:
        def astream_events(self, input, config=None, version=None, stream_mode=None):
            return _aiter([{"event": "on_chain_end", "data": {"output": {"messages": []}}}])

        async def aget_state(self, config):
            return None

    class FakePromptCompiler:
        async def compile_system_prompt_cached(self, **kwargs):
            return "PROMPT"

    class FakeConvSvc:
        async def get_conversation(self, conv_id, user_id=None):
            return object()

        async def reuse_user_turn(
            self, conversation_id, content, message_id, *, user_id=None
        ):
            calls["reused"].append((conversation_id, content, message_id))
            return None

        async def add_message(self, **kwargs):
            calls["added"].append(kwargs)
            return None

    user = User(id=uuid4(), email="a@b.c", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_conversation_service] = lambda: FakeConvSvc()
    monkeypatch.setattr(conv_mod, "orchestrator_graph", FakeGraph())
    monkeypatch.setattr(conv_mod, "prompt_compiler", FakePromptCompiler())
    monkeypatch.setattr(conv_mod, "load_active_skills", _aiter_ok)
    monkeypatch.setattr(run_exec_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)
    run_store = FakeRunStore()
    monkeypatch.setattr(
        conv_mod, "async_session_factory", lambda: FakeRunSession(run_store)
    )

    reuse_id = uuid4()
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            reused_resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={
                    "messages": [{"role": "user", "content": "ask again"}],
                    "mode": "normal",
                    "reuse_user_message_id": str(reuse_id),
                },
            )
            assert reused_resp.status_code == 200, reused_resp.text
            # EOF forces the run to a terminal status, which releases the slot.
            await asyncio.wait_for(reused_resp.aread(), timeout=10.0)

            appended_resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={"messages": [{"role": "user", "content": "new turn"}], "mode": "normal"},
            )
            assert appended_resp.status_code == 200, appended_resp.text
            await asyncio.wait_for(appended_resp.aread(), timeout=10.0)
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_conversation_service, None)

    assert len(calls["reused"]) == 1, "the reuse id never reached the service"
    assert calls["reused"][0][2] == reuse_id
    assert calls["reused"][0][1] == "ask again"
    assert len(calls["added"]) == 1, "the reuse request also appended a duplicate"
    assert calls["added"][0]["content"] == "new turn"


async def test_stream_route_surfaces_a_rejected_reuse_instead_of_appending(
    monkeypatch,
):
    """A stale/foreign id must 404/409 -- never quietly fall back to an append.

    The fail-open guard around the normal append exists so a persistence hiccup
    cannot cost the user their answer. It must not cover the reuse path: falling
    back there recreates the exact duplicate row the reuse exists to prevent, and
    it does so silently.
    """
    from backend.app.api.deps import get_conversation_service, get_current_user
    from backend.app.api.v1 import conversations as conv_mod
    from backend.app.domain.user.models import User
    from backend.app.main import app
    from backend.app.services import run_executor as run_exec_mod
    from backend.tests.fakes import FakeRunSession, FakeRunStore
    from httpx import ASGITransport, AsyncClient

    class FakeGraph:
        def astream_events(self, input, config=None, version=None, stream_mode=None):
            return _aiter([])

        async def aget_state(self, config):
            return None

    added: list = []

    class FakeConvSvc:
        async def get_conversation(self, conv_id, user_id=None):
            return object()

        async def reuse_user_turn(self, *a, **k):
            raise ValueError("not the latest turn")

        async def add_message(self, **kwargs):
            added.append(kwargs)
            return None

    user = User(id=uuid4(), email="a@b.c", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_conversation_service] = lambda: FakeConvSvc()
    monkeypatch.setattr(conv_mod, "orchestrator_graph", FakeGraph())
    monkeypatch.setattr(run_exec_mod, "trace_span", _null_span)
    monkeypatch.setattr(conv_mod, "OrganizationService", _NullOrg)
    run_store = FakeRunStore()
    monkeypatch.setattr(
        conv_mod, "async_session_factory", lambda: FakeRunSession(run_store)
    )

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={
                    "messages": [{"role": "user", "content": "q"}],
                    "mode": "normal",
                    "reuse_user_message_id": str(uuid4()),
                },
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
        app.dependency_overrides.pop(get_conversation_service, None)

    assert resp.status_code == 409, resp.text
    assert added == [], "a refused reuse must not fall back to appending"
    assert run_store.runs == [], "no run should start for a refused request"


# ── the stream route is actually rate limited ────────────────────────────────


async def test_stream_route_enforces_the_rate_limit(monkeypatch):
    """A denied limiter must yield 429, not a streamed answer.

    Regression (project guidelines): the route called ``rate_limit(...)`` --
    a FastAPI *dependency factory* -- as though it were the enforcer. That raised
    ``TypeError: rate_limit() got an unexpected keyword argument 'request'``,
    which the surrounding fail-open ``except`` swallowed, so the chat stream was
    never throttled and the failure was invisible from outside. The limit is now
    a real dependency, so a denied ``check_rate_limit`` aborts the request before
    the run is created. Reverting the dependency and restoring the inline call
    makes this test fail (the denial is ignored and a 200 streams).
    """
    from backend.app.api.deps import get_current_user
    from backend.app.domain.user.models import User
    from backend.app.infrastructure.resilience import rate_limit as rl_mod
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    class _Deny:
        async def check_rate_limit(
            self, identifier, limit=100, window_seconds=60, cost=1
        ):
            return False, 0

    user = User(id=uuid4(), email="a@b.c", hashed_password="x")
    app.dependency_overrides[get_current_user] = lambda: user
    # ``_check_and_enforce`` reads the module-global ``redis_service`` at call
    # time, so re-pointing it here is what the real dependency consults.
    monkeypatch.setattr(rl_mod, "redis_service", _Deny())

    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.post(
                f"/api/v1/conversations/{uuid4()}/stream",
                json={"messages": [{"role": "user", "content": "hi"}], "mode": "normal"},
            )
        assert resp.status_code == 429, resp.text
        assert resp.headers.get("Retry-After") == "60"
    finally:
        app.dependency_overrides.pop(get_current_user, None)


# ── helpers ──────────────────────────────────────────────────────────────────


async def _aiter_ok():
    return []


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

