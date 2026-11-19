"""A HITL approval is a run, not a second-class inline graph call.

The approve endpoint used to call ``graph.ainvoke`` directly: no run frames,
no ``done``, no quality/usage/cost rows, and the assistant reply was persisted
with ``parent_message_id=None``. It also blocked the HTTP request for the whole
resumed graph, which forced the BFF to hold a 150 s socket open.

These tests pin the transport contract: validate the parked interrupt, then
hand the resume to ``execute_run`` as a durable background run and return its
id so the client can rejoin the stream.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from backend.app.api.v1 import conversations
from backend.app.api.v1.conversations import HITLFeedbackRequest


class _FakeSessionFactory:
    def __call__(self):
        return self

    async def __aenter__(self):
        return object()

    async def __aexit__(self, *_args):
        return None


@pytest.fixture(autouse=True)
def _clean_stream_slots():
    yield
    conversations._active_stream_threads.clear()


@pytest.fixture
def harness(monkeypatch):
    user_id = uuid4()
    conversation_id = uuid4()
    run_id = uuid4()
    user_message_id = uuid4()
    captured: dict = {}

    class _FakeRunService:
        def __init__(self, _session):
            pass

        async def start_run(self, **kwargs):
            captured["start"] = kwargs
            return SimpleNamespace(id=run_id)

    class _ConvService:
        async def get_conversation(self, _conversation_id, *, user_id):
            captured["checked_owner"] = user_id

        async def get_messages(self, _conversation_id):
            return [
                SimpleNamespace(id=user_message_id, role="user", content="run my code"),
                SimpleNamespace(id=uuid4(), role="assistant", content="needs approval"),
            ]

    interrupt_payload = {
        "action": "tool_approval",
        "tool_name": "execute_python",
        "arguments": {"code": "print(1)"},
    }
    snapshot = SimpleNamespace(
        tasks=[SimpleNamespace(interrupts=[SimpleNamespace(value=interrupt_payload)])]
    )

    async def _aget_state(_config):
        return snapshot

    async def _ainvoke(*_args, **_kwargs):
        raise AssertionError("the resume must not bypass execute_run")

    graph = SimpleNamespace(aget_state=_aget_state, ainvoke=_ainvoke)

    class _Coro:
        def close(self):
            pass

    def _execute_run(**kwargs):
        # Captured at call time: the executor's body is never awaited here (the
        # endpoint only spawns it), so an async stub would record nothing.
        captured["execute"] = kwargs
        return _Coro()

    def _spawn(coro, *, run_id):
        captured["spawn_run_id"] = run_id
        coro.close()

    monkeypatch.setattr(conversations, "async_session_factory", _FakeSessionFactory())
    monkeypatch.setattr(conversations, "RunService", _FakeRunService)
    monkeypatch.setattr(conversations, "orchestrator_graph", graph)
    monkeypatch.setattr(conversations, "execute_run", _execute_run)
    monkeypatch.setattr(conversations, "spawn_run_task", _spawn)

    return SimpleNamespace(
        user_id=user_id,
        conversation_id=conversation_id,
        run_id=run_id,
        user_message_id=user_message_id,
        conv_svc=_ConvService(),
        captured=captured,
    )


async def test_approval_spawns_a_durable_run_and_returns_its_id(harness):
    result = await conversations.hitl_feedback(
        conversation_id=str(harness.conversation_id),
        body=HITLFeedbackRequest(action="approve", data={"confirmed": True}),
        current_user=SimpleNamespace(id=harness.user_id),
        conv_svc=harness.conv_svc,
    )

    assert result["status"] == "resumed"
    assert result["action"] == "approve"
    assert result["run_id"] == str(harness.run_id)
    assert harness.captured["spawn_run_id"] == harness.run_id
    assert harness.captured["start"] == {
        "conversation_id": harness.conversation_id,
        "user_id": harness.user_id,
        "thread_id": str(harness.conversation_id),
        "mode": "normal",
    }
    assert harness.captured["checked_owner"] == harness.user_id


async def test_resume_input_is_a_command_with_the_approval_decision(harness):
    await conversations.hitl_feedback(
        conversation_id=str(harness.conversation_id),
        body=HITLFeedbackRequest(action="approve", data={"confirmed": True}),
        current_user=SimpleNamespace(id=harness.user_id),
        conv_svc=harness.conv_svc,
    )

    execute = harness.captured["execute"]
    command = execute["graph_input"]
    assert getattr(command, "resume", None) == {
        "action": "approve",
        "data": {"confirmed": True},
    }
    assert execute["run_id"] == harness.run_id
    assert execute["parent_message_id"] == harness.user_message_id
    assert execute["config"]["configurable"] == {
        "thread_id": str(harness.conversation_id),
        "user_id": str(harness.user_id),
    }


async def test_executor_owns_the_stream_slot_until_the_resume_finishes(harness):
    await conversations.hitl_feedback(
        conversation_id=str(harness.conversation_id),
        body=HITLFeedbackRequest(action="approve", data={}),
        current_user=SimpleNamespace(id=harness.user_id),
        conv_svc=harness.conv_svc,
    )

    # The endpoint returned without waiting for the graph, so it may not have
    # released the slot: a second run on this thread would double-execute tools.
    assert str(harness.conversation_id) in conversations._active_stream_threads
    await harness.captured["execute"]["on_finish"]()
    assert str(harness.conversation_id) not in conversations._active_stream_threads
