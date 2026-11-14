"""Every keyword the run's persist path passes must exist on the repository.

## The defect this covers

`_persist_reply` wrote the assistant row with
``conv_svc.add_message(..., cost_usd=cost_usd)``, but
``ConversationRepository.add_message`` had no such parameter. Since
``ConversationService.add_message`` forwards ``**kwargs`` verbatim, nothing
raised until the call itself: every streamed reply hit
``TypeError: add_message() got an unexpected keyword argument 'cost_usd'``,
inside `_persist_reply`'s fail-open ``except``.

The result was the worst possible shape of failure — the answer appeared on
screen and vanished on reload, usage stayed at zero, feedback and regenerate had
nothing to point at, and no request ever returned an error. It went unnoticed
because each side's own tests passed: the executor's tests fake the conversation
service, and the repository's tests never pass a raw cost.

This test closes that seam by reading the call the executor actually makes, so
adding a keyword on one side without the other fails here rather than in
production.
"""
from __future__ import annotations

import ast
import inspect
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.domain.conversation.repository import ConversationRepository  # noqa: E402

_EXECUTOR = Path(__file__).resolve().parents[2] / "app" / "services" / "runs" / "executor.py"


def _add_message_keywords() -> set[str]:
    """Keyword arguments `_persist_reply` passes to `add_message`."""
    tree = ast.parse(_EXECUTOR.read_text(encoding="utf-8"))
    keywords: set[str] = set()
    found = False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = getattr(func, "attr", getattr(func, "id", ""))
        if name != "add_message":
            continue
        found = True
        keywords.update(kw.arg for kw in node.keywords if kw.arg)
    assert found, "no add_message call found in the run executor"
    return keywords


def test_the_persist_call_only_passes_keywords_the_repository_accepts() -> None:
    accepted = set(inspect.signature(ConversationRepository.add_message).parameters)
    passed = _add_message_keywords()

    missing = passed - accepted
    assert not missing, (
        "the run executor passes keywords ConversationRepository.add_message does "
        f"not accept: {sorted(missing)}. Every streamed reply would fail to "
        "persist."
    )
