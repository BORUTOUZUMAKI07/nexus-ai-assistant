"""The test session must not upload traces to LangSmith.

Why this exists as a test rather than a comment: on 2026-10-05 one local
``pytest`` run put **120 spans** into the ``nexus-ai-assistant`` LangSmith
project. Nothing failed -- the suite was green -- and the pollution was only
visible by reading the API back. ``backend/.env`` sets ``LANGSMITH_TRACING=true``
because the *app* should trace, pytest loads that file, and
``tests/test_node_policies.py:81`` compiles a real ``StateGraph`` whose nodes are
instrumented. So every suite run silently mixed pytest fixtures in with real
conversation traces.

Three independent facts make the override in ``tests/conftest.py`` necessary:

1. ``backend/.env`` is untracked, so the fix cannot be "set it to false in the
   file" -- that would disable tracing for the app too, and would be undone by
   anyone who recreates their ``.env`` from ``.env.example``.
2. ``langsmith.utils.get_env_var`` is ``@functools.lru_cache``'d on its
   *arguments*, not on the environment, so the value is frozen at first read.
   Assigning the env var before any backend import is what keeps the read
   correct.
3. ``tracing_is_enabled()`` reads that value, so asserting on it covers both the
   environment and the cache in one behavioral check -- there is deliberately no
   second assertion re-checking ``get_env_var`` directly (an unreachable second
   guard is a second place to be wrong).
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_DOTENV = _BACKEND_DIR / ".env"


def test_the_test_session_env_disables_tracing() -> None:
    """``tests/conftest.py`` must override the flag for the pytest process.

    Reading ``backend/.env`` would give the opposite answer on purpose -- that
    file says ``true`` because the app should trace. This asserts the override
    landed, which is the whole mechanism: no ``pytest-env`` plugin exists in
    this repo (checked), and no new dependency may be added.
    """
    assert os.environ.get("LANGSMITH_TRACING") == "false", (
        "tests/conftest.py must set LANGSMITH_TRACING=false before any backend "
        f"import; it is currently {os.environ.get('LANGSMITH_TRACING')!r}. "
        "Without it every local pytest run uploads ~120 spans to LangSmith."
    )


def test_the_sdk_would_not_trace_from_this_session() -> None:
    """The SDK's own answer must be False -- not merely the env var's value.

    This is the behavioral check, and it is the one that would catch a stale
    ``get_env_var`` cache: ``tracing_is_enabled()`` resolves the cached env
    lookup (``langsmith/utils.py:141``), so a ``true`` read before the override
    would surface here as ``True`` even with the env var already corrected.
    """
    from langsmith import utils as ls_utils

    assert ls_utils.tracing_is_enabled() is False, (
        "langsmith still believes tracing is enabled during tests, so the "
        "session would upload spans. A cached LANGSMITH_TRACING=true read "
        "before tests/conftest.py ran is the likely cause."
    )


def test_the_env_example_still_defaults_tracing_off() -> None:
    """The shipped template must not turn tracing on for a fresh checkout.

    Guarded because ``LANGSMITH_TRACING=true`` in the template would reintroduce
    exactly this pollution for anyone copying ``.env.example`` to ``.env`` --
    including CI images built from it.
    """
    example = _BACKEND_DIR / ".env.example"
    assert example.exists(), "backend/.env.example is missing"
    values = [
        ln.split("=", 1)[1].strip().lower()
        for ln in example.read_text(encoding="utf-8").splitlines()
        if ln.startswith("LANGSMITH_TRACING=")
    ]
    assert values, "backend/.env.example must ship LANGSMITH_TRACING"
    assert values == ["false"], (
        "backend/.env.example must default LANGSMITH_TRACING=false; "
        f"found {values}"
    )


@pytest.mark.skipif(not _DOTENV.exists(), reason="backend/.env is untracked; CI has none")
def test_the_app_still_asks_for_tracing_locally() -> None:
    """Turning the *app's* tracing off is not an acceptable way to fix this.

    The cheap wrong fix for the pollution is to set ``LANGSMITH_TRACING=false``
    in ``backend/.env`` -- which stops the uploads and simultaneously destroys
    the feature the whole LangSmith work exists to provide. The override belongs
    in ``tests/conftest.py`` and must not reach back into the app's own config.

    Reads only the one line: this file also holds ``LANGSMITH_API_KEY``, which
    is a secret and is never printed or asserted on.
    """
    flag = [
        ln.split("=", 1)[1].strip().lower()
        for ln in _DOTENV.read_text(encoding="utf-8").splitlines()
        if ln.startswith("LANGSMITH_TRACING=")
    ]
    assert flag == ["true"], (
        "backend/.env must keep LANGSMITH_TRACING=true so the app traces. "
        "Disable test tracing in tests/conftest.py instead of turning off "
        f"tracing everywhere; found {flag!r}."
    )
