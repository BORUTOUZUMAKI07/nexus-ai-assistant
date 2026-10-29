"""
Structured-output parsing and validation (Gap 1).

The defect: the ARQ constraint check parsed the model reply with
``raw.strip().strip("```json")`` + ``json.loads``. That succeeds only for a
bare, perfectly-formed object. A fence, a leading sentence, trailing prose, or a
brace inside a quoted value made it fail -- and because the caller fails *open*,
the loss was a silently-missed safety/tool/recency flag, never an error.

These tests pin both halves of the fix: the defensive parser
(:mod:`backend.app.services.structured_parse`) and its use at the ARQ site,
including the ``bool("false") is True`` trap.
"""
from __future__ import annotations

from backend.app.services.structured_parse import (
    extract_json_array,
    extract_json_object,
    strip_code_fence,
    validate_object,
)
from pydantic import BaseModel


class _Flags(BaseModel):
    needs_tool: bool = False
    safety_flag: bool = False


# ── extract_json_object ──────────────────────────────────────────────────────


def test_extract_object_from_a_bare_reply():
    assert extract_json_object('{"a": 1}') == {"a": 1}


def test_extract_object_from_a_fenced_reply():
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_extract_object_with_leading_prose():
    """The old ``strip("```json")`` left the sentence glued to the object."""
    raw = 'Here is the JSON you asked for:\n{"a": 1}'
    assert extract_json_object(raw) == {"a": 1}


def test_extract_object_with_trailing_prose():
    raw = '{"a": 1}\nLet me know if you need anything else.'
    assert extract_json_object(raw) == {"a": 1}


def test_extract_object_ignores_a_brace_inside_a_string():
    """A brace inside a quoted value must not truncate the object.

    The brace is deliberately **unbalanced** (``{`` with no ``}``). A brace pair
    inside a string is indistinguishable from real structure to a naive depth
    counter, so a balanced one would pass either way and prove nothing; an
    unbalanced one makes the string-aware scan load-bearing.
    """
    raw = 'Prefix: {"math": "use { only", "n": 2}'
    assert extract_json_object(raw) == {"math": "use { only", "n": 2}


def test_extract_object_keeps_an_escaped_quote_before_a_brace():
    """An escaped quote must not be read as the end of the string.

    Treating ``\\"`` as a closing quote ends the string early, so the ``}`` that
    follows it inside the value is counted as real structure and the span closes
    on invalid JSON. Only escape-aware scanning returns the whole object.
    """
    raw = 'Here: {"a": "say \\"}\\" done", "b": 2}'
    assert extract_json_object(raw) == {"a": 'say "}" done', "b": 2}


def test_extract_object_handles_an_escaped_quote():
    raw = '{"quote": "he said \\"hi\\"", "ok": true}'
    assert extract_json_object(raw) == {"quote": 'he said "hi"', "ok": True}


def test_extract_object_returns_none_for_garbage():
    for bad in ("", "no json here", "[1, 2, 3]", "{unterminated"):
        assert extract_json_object(bad) is None


def test_extract_object_returns_none_for_a_bare_scalar():
    assert extract_json_object("42") is None


def test_extract_object_never_raises_on_none():
    assert extract_json_object(None) is None


# ── extract_json_array ───────────────────────────────────────────────────────


def test_extract_array_from_prose_and_fence():
    assert extract_json_array('Sure: ["a", "b"] done') == ["a", "b"]
    assert extract_json_array('```json\n["x", "y"]\n```') == ["x", "y"]


def test_extract_array_returns_none_for_garbage():
    assert extract_json_array("nope") is None
    assert extract_json_array('{"a": 1}') is None


# ── strip_code_fence ─────────────────────────────────────────────────────────


def test_strip_code_fence_without_a_fence_is_trim_only():
    assert strip_code_fence("  hello  ") == "hello"


# ── validate_object ──────────────────────────────────────────────────────────


def test_validate_object_parses_string_booleans_the_right_way():
    """``"false"`` must become ``False`` -- a raw ``bool()`` would make it True."""
    flags = validate_object('{"needs_tool": "false", "safety_flag": "true"}', _Flags)
    assert flags is not None
    assert flags.needs_tool is False
    assert flags.safety_flag is True


def test_validate_object_defaults_missing_keys():
    flags = validate_object('{"needs_tool": true}', _Flags)
    assert flags is not None
    assert flags.needs_tool is True
    assert flags.safety_flag is False


def test_validate_object_rejects_an_unusable_value():
    assert validate_object('{"needs_tool": "maybe"}', _Flags) is None


# ── the ARQ call site ────────────────────────────────────────────────────────


async def test_arq_parses_a_prose_wrapped_fenced_reply(monkeypatch):
    from backend.app.agents.orchestrator import nodes as nodes_mod

    captured: dict = {}

    async def fake(messages, **kwargs):
        captured.update(kwargs)
        return 'Sure:\n```json\n{"safety_flag": true, "needs_tool": false}\n```'

    monkeypatch.setattr(nodes_mod.ai_client, "completion", fake)

    result = await nodes_mod._run_arq("delete all my files")

    assert result["safety_flag"] is True
    assert result["needs_tool"] is False
    # Provider-enforced JSON was requested (layer 1 of the fix).
    assert captured.get("response_format") == {"type": "json_object"}


async def test_arq_fails_open_on_an_unparseable_reply(monkeypatch):
    from backend.app.agents.orchestrator import nodes as nodes_mod

    async def fake(messages, **kwargs):
        return "I cannot help with that."

    monkeypatch.setattr(nodes_mod.ai_client, "completion", fake)

    assert await nodes_mod._run_arq("anything") == {
        "needs_tool": False,
        "safety_flag": False,
        "recency_needed": False,
        "ambiguous": False,
    }


async def test_arq_fails_open_on_an_invalid_boolean(monkeypatch):
    """A value the model cannot answer with a boolean must not be coerced."""
    from backend.app.agents.orchestrator import nodes as nodes_mod

    async def fake(messages, **kwargs):
        return '{"safety_flag": "maybe"}'

    monkeypatch.setattr(nodes_mod.ai_client, "completion", fake)

    assert (await nodes_mod._run_arq("x"))["safety_flag"] is False
