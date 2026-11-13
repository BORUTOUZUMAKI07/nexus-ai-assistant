"""Per-turn state hygiene, and the `message.completed` webhook on the run path.



Both were reproduced live before they were fixed (docs/live-verification.md):



* a Web-mode question about the newest Python release was answered with the

  *previous* turn's document phrase, because `citations` -- which the synthesizer

  splices into its prompt under "Answer from the block above" -- survived the

  checkpoint into the next turn;

* a registered webhook received nothing for a real chat turn, because

  `dispatch_event("message.completed")` was only called from the non-streaming

  endpoint the app does not use.

"""



from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any
from uuid import uuid4

import pytest
from langchain_core.messages import AIMessage, HumanMessage


@pytest.fixture

def _runnable(monkeypatch):

    """`bootstrap_node` reads LangGraph's run config, which needs a run context."""

    from backend.app.agents.orchestrator import nodes



    monkeypatch.setattr(nodes, "get_config", lambda: {"configurable": {}})





STALE_TURN: dict[str, Any] = {

    "messages": [],

    "citations": [{"filename": "composer-note.txt", "content_snippet": "VIOLET-MARLIN-42"}],

    "tool_results": [{"tool_name": "web_search", "status": "ok"}],

    "evidence_score": 0.4,

    "evidence_gate_passed": False,

    "revision_count": 2,

    "critique": {"notes": "stale"},

    "grader_verdict": "unrelated",

    "needs_web_search": True,

    "answer_model": "gemini-3.5-flash",

    "artifact_id": "a-1",

    "artifact_created": True,

}





async def test_a_new_user_turn_does_not_inherit_the_previous_turn(_runnable):

    """The checkpoint is per conversation; findings must not follow it."""

    from backend.app.agents.orchestrator import nodes



    state = {**STALE_TURN, "messages": [HumanMessage(content="what is the newest python?")]}

    out = await nodes.bootstrap_node(state)



    assert out["citations"] == []

    assert out["tool_results"] == []

    assert out["evidence_score"] == 0.0

    assert out["evidence_gate_passed"] is None

    assert out["revision_count"] == 0

    assert out["critique"] is None

    assert out["grader_verdict"] == "relevant"

    assert out["needs_web_search"] is False

    assert out["answer_model"] == ""

    assert out["artifact_id"] is None

    assert out["artifact_created"] is False





async def test_a_hitl_resume_keeps_the_turn_it_is_resuming(_runnable):

    """A resume re-enters bootstrap with the interrupted turn's state on purpose.



    Dropping `tool_results` there would discard the approved tool's output and

    re-run the graph as though nothing had been authorised.

    """

    from backend.app.agents.orchestrator import nodes



    state = {**STALE_TURN, "messages": [AIMessage(content="paused")]}

    out = await nodes.bootstrap_node(state)



    assert out["tool_results"] == state["tool_results"]

    assert out["citations"] == state["citations"]





async def test_a_finished_run_dispatches_message_completed(monkeypatch):

    """The run path -- the one the UI chats through -- must deliver the event."""

    from backend.app.services.runs import executor as run_executor
    from backend.app.services.webhooks import service as webhook_service



    calls: list[dict[str, Any]] = []



    class _FakeWebhooks:

        def __init__(self, _session: Any) -> None:

            pass



        async def dispatch_event(self, *, event: str, payload: dict, user_id: Any) -> int:

            calls.append({"event": event, "payload": payload, "user_id": user_id})

            return 1



    @asynccontextmanager

    async def _fake_session_factory():

        yield object()



    monkeypatch.setattr(webhook_service, "WebhookService", _FakeWebhooks)

    monkeypatch.setattr(run_executor, "async_session_factory", _fake_session_factory)



    user_id, conversation_id, message_id = uuid4(), uuid4(), uuid4()

    await run_executor._dispatch_message_completed(

        user_id=user_id,

        conversation_id=conversation_id,

        message_id=message_id,

        model="openai/gpt-oss-120b",

        content="the answer",

    )



    assert len(calls) == 1

    assert calls[0]["event"] == "message.completed"

    assert calls[0]["user_id"] == user_id

    assert calls[0]["payload"] == {

        "conversation_id": str(conversation_id),

        "message_id": str(message_id),

        "role": "assistant",

        "model": "openai/gpt-oss-120b",

        "content": "the answer",

    }





async def test_a_webhook_failure_never_raises(monkeypatch):

    """A dead endpoint must not take the turn down with it."""

    from backend.app.services.runs import executor as run_executor
    from backend.app.services.webhooks import service as webhook_service



    class _BrokenWebhooks:

        def __init__(self, _session: Any) -> None:

            pass



        async def dispatch_event(self, **_kwargs: Any) -> int:

            raise RuntimeError("endpoint exploded")



    @asynccontextmanager

    async def _fake_session_factory():

        yield object()



    monkeypatch.setattr(webhook_service, "WebhookService", _BrokenWebhooks)

    monkeypatch.setattr(run_executor, "async_session_factory", _fake_session_factory)



    await run_executor._dispatch_message_completed(

        user_id=uuid4(),

        conversation_id=uuid4(),

        message_id=uuid4(),

        model="m",

        content="c",

    )

