"""The test session must not export logs to New Relic.

The third of the three telemetry guards in ``tests/conftest.py`` (after
``LANGSMITH_TRACING`` and ``SENTRY_DSN``), and the defect it guards has their
shape exactly: a guard that runs too late to work, on a sink nobody was
watching.

``backend/.env`` sets ``NEW_RELIC_ENABLED=true`` because the *app* should
export, and pytest loads that file. conftest's first backend import therefore
builds ``settings`` with the flag true, ``main.py:40`` runs ``setup_logging()``
and the ``logging.py:183`` gate attaches the OTLP ``LoggingHandler`` to the
ROOT logger at ``DEBUG`` (``LOG_LEVEL`` unset + ``ENVIRONMENT`` development ->
``logging.py:84``). Everything the session logs then leaves the machine — and
most of it is not the app's own lines: faker's autouse session fixture
(``faker/contrib/pytest/plugin.py:9-20``) constructs a ``Faker`` in EVERY
session and emits 44 DEBUG locale messages before the first test runs. Measured
2026-10-07: one single-file session produced exactly one 47-record batch in the
collector (44 faker + 3 app-init), so every run shipped that.

Two tests, deliberately different in what they can prove:

* ``test_the_settings_singleton_never_sees_export_enabled`` is behavioural —
  it asks the singleton what it actually holds. It only discriminates where
  ``backend/.env`` turns export ON at all, so it SKIPs with that reason rather
  than passing vacuously on a checkout that does not (CI has no ``.env``, and
  the config default is already ``False``).
* ``test_the_new_relic_guard_precedes_every_backend_import`` reads the source
  through ``ast`` — not a regex, per this repo's CRLF rule — and is load-bearing
  everywhere regardless of what any ``.env`` contains.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CONFTEST = _BACKEND_DIR / "tests" / "conftest.py"
_DOTENV = _BACKEND_DIR / ".env"

_TRUTHY = {"1", "true", "yes", "on"}


def _dotenv_enables_export() -> bool:
    """Whether ``backend/.env`` turns New Relic log export ON.

    Read only to decide whether the behavioural test *can* discriminate: where
    the file does not enable export, a conftest with no guard at all would
    still hold ``False`` and the assertion would pass for the wrong reason.
    """
    if not _DOTENV.exists():
        return False
    for line in _DOTENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("NEW_RELIC_ENABLED="):
            return line.split("=", 1)[1].strip().lower() in _TRUTHY
    return False


@pytest.mark.skipif(
    not _dotenv_enables_export(),
    reason="backend/.env does not enable New Relic log export, so a guard that runs too late would be unobservable here",
)
def test_the_settings_singleton_never_sees_export_enabled() -> None:
    """Behavioural half: what the already-constructed singleton actually holds.

    ``settings`` was built during ``conftest``'s first backend import, so this
    reads the effect of the guard's *position* rather than the env var — the
    env var is set either way once ``conftest`` has finished loading, and
    asserting on it would pass with the guard in the wrong place.
    """
    from backend.app.core.config import settings

    assert settings.NEW_RELIC_ENABLED is False, (
        "settings.NEW_RELIC_ENABLED is true, so logging.py:183 attaches the "
        "OTLP handler to the root logger at DEBUG and every test session "
        "ships its logs (44 of them faker locale messages) to New Relic. "
        "tests/conftest.py must assign it BEFORE its first backend import — "
        "redis_client builds the settings singleton at import time, so an "
        "assignment afterwards has already been beaten to backend/.env's "
        "'true'."
    )


def _guard_and_import_lines(tree: ast.AST) -> tuple[list[int], list[int]]:
    """Line numbers of the NEW_RELIC_ENABLED assignment and of every backend import."""
    guards: list[int] = []
    imports: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Subscript)
            and isinstance(node.targets[0].value, ast.Attribute)
            and node.targets[0].value.attr == "environ"
            and isinstance(node.targets[0].value.value, ast.Name)
            and node.targets[0].value.value.id == "os"
            and isinstance(node.targets[0].slice, ast.Constant)
            and node.targets[0].slice.value == "NEW_RELIC_ENABLED"
        ):
            guards.append(node.lineno)

        modules: list[str] = []
        if isinstance(node, ast.Import):
            modules = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            modules = [node.module or ""]
        if any(name.split(".")[0] == "backend" for name in modules):
            imports.append(node.lineno)
    return guards, imports


def test_the_new_relic_guard_precedes_every_backend_import() -> None:
    """Ordering half, load-bearing even where no ``.env`` exists.

    The whole mechanism is *position*, so this asserts the position directly.
    Both failure directions are reachable and neither is vacuous: a conftest
    with the assignment deleted finds no guard line, and one with no backend
    import at all finds no import line — each fails loudly instead of passing.
    """
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    guards, imports = _guard_and_import_lines(tree)

    assert guards, (
        'tests/conftest.py no longer contains `os.environ["NEW_RELIC_ENABLED"] = "false"`; '
        "without it every local pytest run ships its logs to New Relic"
    )
    assert imports, (
        "tests/conftest.py imports no backend module at all — nothing builds "
        "the settings singleton, so this ordering check would be vacuous"
    )
    assert min(guards) < min(imports), (
        f"the NEW_RELIC_ENABLED guard is on line {min(guards)} but the first backend "
        f"import is on line {min(imports)}. redis_client constructs the "
        "settings singleton at import time, so a later guard cannot affect "
        "what it read."
    )
