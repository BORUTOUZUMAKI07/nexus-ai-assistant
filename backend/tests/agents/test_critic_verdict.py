"""Critic verdict parsing must be structural, and a critic outage must not
crash the NO_RETRY synthesizer node.
"""
from __future__ import annotations

import pytest
from backend.app.agents.subagents import critic as critic_module


@pytest.fixture
def critic():
    return critic_module.CriticSubagent()


async def _respond(monkeypatch, text: str | Exception):
    async def _completion(**_kwargs):
        if isinstance(text, Exception):
            raise text
        return text

    monkeypatch.setattr(critic_module.ai_client, "completion", _completion)


async def test_approved_verdict_with_score_is_parsed(critic, monkeypatch):
    await _respond(monkeypatch, "STATUS: APPROVED\nSCORE: 91\nFEEDBACK: good")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is True
    assert verdict["score"] == 91
    assert verdict["critique"] == "STATUS: APPROVED\nSCORE: 91\nFEEDBACK: good"


async def test_revision_verdict_is_not_approved_and_keeps_score(critic, monkeypatch):
    await _respond(monkeypatch, "STATUS: NEEDS_REVISION\nSCORE: 42\nFEEDBACK: missing detail")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is False
    assert verdict["score"] == 42


async def test_lowercase_status_is_parsed(critic, monkeypatch):
    await _respond(monkeypatch, "status: approved\nscore: 77\nfeedback: ok")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is True
    assert verdict["score"] == 77


async def test_approval_mentioned_outside_the_status_line_does_not_approve(critic, monkeypatch):
    await _respond(monkeypatch, "STATUS: NEEDS_REVISION\nSCORE: 30\nFEEDBACK: an APPROVED answer would be better")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is False


async def test_invalid_or_missing_score_is_none_not_zero(critic, monkeypatch):
    await _respond(monkeypatch, "STATUS: APPROVED\nSCORE: n/a\nFEEDBACK: ok")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is True
    assert verdict["score"] is None


async def test_missing_status_fails_closed(critic, monkeypatch):
    await _respond(monkeypatch, "SCORE: 88\nFEEDBACK: no status line")
    verdict = await critic.evaluate("req", "draft")

    assert verdict["approved"] is False


async def test_provider_failure_does_not_raise_and_is_reported(critic, monkeypatch):
    await _respond(monkeypatch, RuntimeError("provider down"))
    verdict = await critic.evaluate("req", "draft")

    assert verdict["error"] == "provider down"
    # The quality critic is not a safety gate: accept the draft, record that it
    # was unreviewed, and never feed an exception string back as revision text.
    assert verdict["approved"] is True
    assert verdict["score"] is None
