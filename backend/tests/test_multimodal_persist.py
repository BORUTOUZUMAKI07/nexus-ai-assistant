"""A vision turn must persist its answer, not raise inside the persistence call.

## The defect this covers

``_persist_reply`` built its prompt text with
``" ".join(m["content"] for m in user_messages)``. ``content`` is a plain string
for a text turn and a **list of parts** for a multimodal (vision) turn, so the
join raised::

    TypeError: sequence item 0: expected str instance, list found

``_persist_reply`` wraps its whole body in a broad ``except`` whose only job is
to keep a persistence hiccup from costing the user a reply that was already
streamed -- so the error was swallowed, logged as
``agentic_assistant_message_persist_failed``, and **no assistant message, usage
row or cost row was written** for any vision turn. The answer appeared on
screen and then vanished on the next reload.

The fix flattens the parts first (``_message_text``), which is also what the
quality evaluator needs: it was handed the raw list as ``last_user``.

Run: ``cd backend && uv run pytest tests/test_multimodal_persist.py``
"""
from __future__ import annotations

import asyncio
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.services.run_executor import _message_text, _persist_reply  # noqa: E402

# -- the content flattener --------------------------------------------------


def test_plain_string_content_is_returned_unchanged():
    assert _message_text({"content": "what colour is this"}) == "what colour is this"


def test_a_multimodal_turn_yields_its_text_parts():
    # The shape a vision turn actually arrives in (see AGENTS.md: the route
    # passes ``body.messages`` verbatim, so this is the request body).
    message = {
        "content": [
            {"type": "text", "text": "what colour is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    }
    text = _message_text(message)
    assert text == "what colour is this"
    # The image data URL must NOT leak into the prompt text used for token
    # accounting or the quality heuristic -- it is a base64 blob, not words.
    assert "base64" not in text
    assert "data:image" not in text


def test_mixed_parts_are_joined_in_order():
    message = {
        "content": [
            {"type": "text", "text": "first"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            {"type": "text", "text": "second"},
        ]
    }
    assert _message_text(message) == "first second"


def test_a_bare_string_part_is_kept():
    assert _message_text({"content": ["alpha", "beta"]}) == "alpha beta"


def test_empty_and_odd_content_does_not_raise():
    # ``_persist_reply`` must never raise on the shape of history it is handed:
    # an old checkpoint, a tool message, a None. Empty text is fine here -- the
    # caller skips empty *replies*, not empty prompts.
    assert _message_text({"content": []}) == ""
    assert _message_text({"content": None}) == ""
    assert _message_text({"content": 7}) == "7"
    assert _message_text("plain string message") == "plain string message"


# -- the row it writes ------------------------------------------------------


class _Row:
    def __init__(self, row_id: Any) -> None:
        self.id = row_id


class _ConversationService:
    WRITTEN: list[dict[str, Any]] = []

    def __init__(self, session: Any) -> None:
        self.session = session

    async def add_message(self, **kwargs: Any) -> _Row:
        type(self).WRITTEN.append(kwargs)
        return _Row(uuid4())


class _Session:
    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc: Any) -> bool:
        return False


class _UsageService:
    LOGGED: list[Any] = []

    def __init__(self, session: Any) -> None:
        self._repo = self

    async def log_usage(self, *args: Any, **kwargs: Any) -> None:
        type(self).LOGGED.append((args, kwargs))


class _StubCost:
    @staticmethod
    def calculate_cost(*args: Any) -> float:
        return 0.0

    @staticmethod
    async def record_cost_log(**kwargs: Any) -> None:
        return None


class _Quality:
    """Records the prompt the evaluator was handed."""

    seen: list[Any] = []

    @staticmethod
    def evaluate_response_quality(user_text: Any, *args: Any) -> dict[str, Any]:
        _Quality.seen.append(user_text)
        return {}


@asynccontextmanager
async def _null_span(name: Any = "", attrs: Any = None):
    yield


@pytest.fixture(autouse=True)
def _stub_the_row_writers(monkeypatch: pytest.MonkeyPatch):
    import backend.app.services.run_executor as ex

    _ConversationService.WRITTEN = []
    _UsageService.LOGGED = []
    _Quality.seen = []
    monkeypatch.setattr(ex, "ConversationService", _ConversationService)
    monkeypatch.setattr(ex, "UsageService", _UsageService)
    monkeypatch.setattr(ex, "cost_tracking_service", _StubCost)
    monkeypatch.setattr(ex, "quality_service", _Quality)
    monkeypatch.setattr(ex.ai_client, "count_tokens", lambda *a, **k: 1)
    monkeypatch.setattr(ex, "trace_span", _null_span)
    return _ConversationService.WRITTEN


def _reply_kwargs(**over: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "conversation_id": uuid4(),
        "user_id": uuid4(),
        "org_id": None,
        "user_messages": [{"content": "q"}],
        "emitted_text": "the answer",
        "latency_ms": 1.0,
        "parent_message_id": None,
        "run_id": uuid4(),
        "session_factory": _Session,
    }
    kwargs.update(over)
    return kwargs


def test_a_vision_turn_still_writes_the_assistant_row():
    # Before the fix this call raised inside the broad except, wrote nothing,
    # and the answer was lost on reload. The assertion is on the *row*, not on
    # the absence of an exception, because the exception is deliberately
    # swallowed by the caller (AGENTS.md A9.14: assert what reached the writer).
    vision_user = {
        "content": [
            {"type": "text", "text": "what colour is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    }

    asyncio.run(_persist_reply(**_reply_kwargs(user_messages=[vision_user])))

    written = _ConversationService.WRITTEN
    assert len(written) == 1, "the vision turn wrote no assistant message"
    assert written[0]["content"] == "the answer"
    assert written[0]["role"] == "assistant"


def test_a_vision_turn_writes_its_usage_row():
    # The usage row is the *other* thing a vision turn silently lost. An
    # isolated "message row exists" test would not have caught this: the fix is
    # one line above both writes, and a future refactor that re-raises would
    # pass the row test only if the row were written before the usage call.
    vision_user = {
        "content": [
            {"type": "text", "text": "describe"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    }

    asyncio.run(_persist_reply(**_reply_kwargs(user_messages=[vision_user])))

    assert len(_UsageService.LOGGED) == 1
    args, _ = _UsageService.LOGGED[0]
    # first positional arg is the UsageLogCreate; check it carried tokens
    assert getattr(args[0], "prompt_tokens", None) == 1


def test_the_quality_evaluator_is_handed_text_not_a_parts_list():
    # This is the second half of the defect and the reason the helper exists
    # rather than a one-line cast: ``last_user`` was the raw list, so the
    # quality heuristic ran against a list, not the user's words.
    vision_user = {
        "content": [
            {"type": "text", "text": "what colour is this"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
        ]
    }

    asyncio.run(_persist_reply(**_reply_kwargs(user_messages=[vision_user])))

    assert _Quality.seen == ["what colour is this"]
