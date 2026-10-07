"""The test session must not initialise Sentry.

The sibling of ``test_no_test_tracing.py`` for the other telemetry sink, and
the defect it guards has the same shape: a guard that runs too late to work.

``tests/conftest.py`` assigns ``os.environ["SENTRY_DSN"] = ""`` so tests never
ship errors to Sentry — and so sentry_sdk's logging integration cannot emit
its ``--- Logging error --- / ValueError: I/O operation on closed file`` stack
at interpreter shutdown. That only holds if the assignment lands before the
FIRST backend import, because ``app/infrastructure/cache/redis_client.py:4``
does::

    from backend.app.core.config import settings

``config.py:740`` builds ``settings = Settings()`` at module level, so the
singleton is constructed during that import and reads ``backend/.env`` — which
on this machine carries a real 95-character DSN. ``main.py:44`` then guards on
``settings.SENTRY_DSN``, so an assignment placed after that import changes
nothing observable and Sentry still initialises for every test.

Two tests, deliberately different in what they can prove:

* ``test_the_settings_singleton_never_sees_the_dsn`` is behavioural — it asks
  the singleton what it actually holds. It is only discriminating where
  ``backend/.env`` defines a DSN at all, so it SKIPs with that reason rather
  than passing vacuously on a checkout that has none (CI has no ``.env``).
* ``test_the_sentry_guard_precedes_every_backend_import`` reads the source
  through ``ast`` — not a regex, per this repo's CRLF rule — and is load-bearing
  everywhere regardless of which DSN, if any, exists.

Neither asserts on the DSN's value: it is a secret and is never printed.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_BACKEND_DIR = Path(__file__).resolve().parents[1]
_CONFTEST = _BACKEND_DIR / "tests" / "conftest.py"
_DOTENV = _BACKEND_DIR / ".env"


def _dotenv_dsn() -> str | None:
    """The ``SENTRY_DSN`` value in ``backend/.env``, or None if absent/empty.

    Read only to decide whether the behavioural test *can* discriminate. The
    value is a secret: it is returned for that boolean decision and never
    printed or asserted on.
    """
    if not _DOTENV.exists():
        return None
    for line in _DOTENV.read_text(encoding="utf-8").splitlines():
        if line.startswith("SENTRY_DSN="):
            return line.split("=", 1)[1].strip() or None
    return None


@pytest.mark.skipif(
    _dotenv_dsn() is None,
    reason="backend/.env defines no SENTRY_DSN, so a guard that runs too late would be unobservable here",
)
def test_the_settings_singleton_never_sees_the_dsn() -> None:
    """Behavioural half: what the already-constructed singleton actually holds.

    ``settings`` was built during ``conftest``'s first backend import, so this
    reads the effect of the guard's *position* rather than the env var — the
    env var is set either way once ``conftest`` has finished loading, and
    asserting on it would pass with the guard in the wrong place.
    """
    from backend.app.core.config import settings

    assert not settings.SENTRY_DSN, (
        "settings.SENTRY_DSN is non-empty, so main.py:44 initialises Sentry "
        "during tests. tests/conftest.py must assign it BEFORE its first "
        "backend import — redis_client builds the settings singleton at import "
        "time, so an assignment afterwards has already been beaten to the "
        "value by backend/.env. The DSN itself is a secret and is not shown."
    )


def _guard_and_import_lines(tree: ast.AST) -> tuple[list[int], list[int]]:
    """Line numbers of the SENTRY_DSN assignment and of every backend import."""
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
            and node.targets[0].slice.value == "SENTRY_DSN"
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


def test_the_sentry_guard_precedes_every_backend_import() -> None:
    """Ordering half, load-bearing even where no DSN exists.

    The whole mechanism is *position*, so this asserts the position directly.
    Both failure directions are reachable and neither is vacuous: a conftest
    with the assignment deleted finds no guard line, and one with no backend
    import at all finds no import line — each fails loudly instead of passing.
    """
    tree = ast.parse(_CONFTEST.read_text(encoding="utf-8"))
    guards, imports = _guard_and_import_lines(tree)

    assert guards, (
        'tests/conftest.py no longer contains `os.environ["SENTRY_DSN"] = ""`; '
        "without it every local pytest run initialises Sentry"
    )
    assert imports, (
        "tests/conftest.py imports no backend module at all — nothing builds "
        "the settings singleton, so this ordering check would be vacuous"
    )
    assert min(guards) < min(imports), (
        f'the SENTRY_DSN guard is on line {min(guards)} but the first backend '
        f"import is on line {min(imports)}. redis_client constructs the "
        "settings singleton at import time, so a later guard cannot affect "
        "what it read."
    )
