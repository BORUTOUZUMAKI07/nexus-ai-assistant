"""The per-thread stream slot must not leak when a run fails to start.

The slot used to be acquired at the top of ``stream_conversation`` but was only
released by the executor's ``on_finish`` callback. Any rejection or failure
between those two points (guardrail 422, stale reuse id 409, a failing
``start_run``/``spawn_run_task``) left the thread permanently 409-ed. The slot
is now acquired immediately before the run starts and released on any failure.
"""
import uuid
from types import SimpleNamespace

import pytest
from backend.app.api.v1 import conversations as conv_api


class _DummySession:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, *args):
        return False


class _FakeOrgService:
    def __init__(self, session):
        pass

    async def resolve_org_id(self, user_id):
        return None


class _FailingRunService:
    def __init__(self, session):
        pass

    async def start_run(self, **kwargs):
        raise RuntimeError("start_run exploded")


class _FakeConvService:
    async def get_conversation(self, conversation_id, user_id):
        return SimpleNamespace(system_prompt=None, model="fast_chat")

    async def add_message(self, **kwargs):
        return SimpleNamespace(id=uuid.uuid4())


@pytest.fixture(autouse=True)
def _clean_slots():
    conv_api._active_stream_threads.clear()
    yield
    conv_api._active_stream_threads.clear()


@pytest.mark.asyncio
async def test_stream_slot_released_when_run_start_fails(monkeypatch):
    thread_id = str(uuid.uuid4())
    user = SimpleNamespace(id=uuid.uuid4())
    body = conv_api.StreamChatRequest(messages=[{"role": "user", "content": "hi"}])

    async def _skills():
        return []

    async def _compile(**kwargs):
        return "compiled"

    monkeypatch.setattr(conv_api, "load_active_skills", _skills)
    monkeypatch.setattr(conv_api.prompt_compiler, "compile_system_prompt_cached", _compile)
    monkeypatch.setattr(conv_api.guardrail_service, "validate_input", lambda text: text)
    monkeypatch.setattr(conv_api, "OrganizationService", _FakeOrgService)
    monkeypatch.setattr(conv_api, "RunService", _FailingRunService)
    monkeypatch.setattr(conv_api, "async_session_factory", _DummySession)

    with pytest.raises(RuntimeError, match="start_run exploded"):
        await conv_api.stream_conversation(
            thread_id, body, user, _FakeConvService(), SimpleNamespace(session=object()), None
        )

    # The executor never ran, so nothing else could have released the slot.
    assert await conv_api._acquire_stream_slot(thread_id) is True
    await conv_api._release_stream_slot(thread_id)
