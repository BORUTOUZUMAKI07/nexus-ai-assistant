"""Strix finding #6 -- Mako must resolve to a version patched for
CVE-2026-102991 (a Windows drive-letter path traversal in TemplateLookup).

Mako reaches this project only through alembic and is never imported at app
runtime, so there is no behaviour to exercise: the guard is that both the
declared floor (revert ``S6/R1``) and the locked resolution (revert ``S6/R2``)
stay at or above the patched 1.4.2. The direct floor is the load-bearing half
-- alembic places no upper or lower bound on Mako at all, so without a
requirement of our own nothing stops the next ``uv lock`` from resolving
1.4.1 again, which is exactly how the vulnerable version was there in the
first place.
"""

import re
import tomllib
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PATCHED = (1, 4, 2)


def _parse(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.strip().split("."))


def _declared_mako_floor() -> str:
    """The ``>=`` floor on the direct mako requirement, or an assertion."""
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    for dep in data["project"]["dependencies"]:
        requirement = dep.split("#", 1)[0].strip()
        if re.match(r"^mako\b", requirement, re.IGNORECASE):
            match = re.search(r">=([0-9][0-9.]*)", requirement)
            assert match, f"mako must carry a >= floor, found {requirement!r}"
            return match.group(1)
    raise AssertionError(
        "mako must be a direct dependency: alembic does not constrain it, so an "
        "unpinned transitive dependency resolves whatever the resolver likes."
    )


def test_mako_floor_is_patched_for_cve_2026_102991():
    """Load-bearing: revert S6/R1 lowers the declared floor to the old 1.4.1."""
    floor = _declared_mako_floor()
    assert _parse(floor) >= PATCHED, (
        f"the declared mako floor is {floor}, but CVE-2026-102991 is patched in "
        "1.4.2"
    )


def test_the_lock_resolves_a_patched_mako():
    """Load-bearing: revert S6/R2 puts 1.4.1 back in uv.lock."""
    lock = (BACKEND / "uv.lock").read_text(encoding="utf-8")
    match = re.search(r'\[\[package\]\]\s+name = "mako"\s+version = "([0-9][0-9.]*)"', lock)
    assert match, "mako must be a resolved package in uv.lock"
    assert _parse(match.group(1)) >= PATCHED, (
        f"uv.lock resolves mako {match.group(1)}, but CVE-2026-102991 is patched "
        "in 1.4.2"
    )


# ── SQLModel: pinned exactly, because a floor shipped the wrong version ──────
#
# Production 500 on every conversation insert, 2026-11-08:
#
#   StatementError: (builtins.ValueError) Datetime values must have timezone
#   information. Use datetime.now(timezone.utc), or annotate the field with
#   NaiveDatetime for naive storage.
#
# The check that raised it lives in sqlmodel/sql/sqltypes.py of the image, and
# that check does not exist in the 0.0.42 this suite runs against: `pip install .`
# in the Dockerfile ignores uv.lock and resolved the newest release allowed by
# the old `>=0.0.21` floor. The app stores naive UTC on purpose (55 model fields,
# 84 `.replace(tzinfo=None)` call sites), so the version is a behavioural
# contract, not a preference — hence an exact pin, asserted here rather than
# trusted to a comment.

SQLMODEL_PIN = "0.0.42"


def _declared_sqlmodel_requirement() -> str:
    data = tomllib.loads((BACKEND / "pyproject.toml").read_text(encoding="utf-8"))
    for dep in data["project"]["dependencies"]:
        requirement = dep.split("#", 1)[0].strip()
        if re.match(r"^sqlmodel\b", requirement, re.IGNORECASE):
            return requirement
    raise AssertionError("sqlmodel must be a direct dependency")


def test_sqlmodel_is_pinned_exactly_not_a_floor():
    """Load-bearing: `>=0.0.21` is what put an untested version in the image."""
    requirement = _declared_sqlmodel_requirement()
    assert requirement == f"sqlmodel=={SQLMODEL_PIN}", (
        "sqlmodel must be pinned exactly: the Dockerfile installs with "
        f"`pip install .`, which ignores uv.lock, so any floor lets the image "
        f"resolve a version this suite never ran against. Found {requirement!r}."
    )


def test_the_lock_agrees_with_the_sqlmodel_pin():
    lock = (BACKEND / "uv.lock").read_text(encoding="utf-8")
    assert f'{{ name = "sqlmodel", specifier = "=={SQLMODEL_PIN}" }}' in lock, (
        "uv.lock must carry the same exact sqlmodel pin as pyproject.toml"
    )
    match = re.search(
        r'\[\[package\]\]\s+name = "sqlmodel"\s+version = "([0-9][0-9.]*)"', lock
    )
    assert match, "sqlmodel must be a resolved package in uv.lock"
    assert match.group(1) == SQLMODEL_PIN, (
        f"uv.lock resolves sqlmodel {match.group(1)}, expected {SQLMODEL_PIN}"
    )


def test_the_installed_sqlmodel_is_the_pinned_one():
    """The half that makes the other two matter.

    A pin in pyproject.toml describes what should be installed; this venv is
    what the tests actually exercise. If they disagree, every green run here is
    a statement about a version that is not deployed — which is exactly how this
    bug reached production with 1579 passing tests.
    """
    import sqlmodel

    assert sqlmodel.__version__ == SQLMODEL_PIN, (
        f"the test environment has sqlmodel {sqlmodel.__version__}, but "
        f"pyproject.toml pins {SQLMODEL_PIN}; re-sync the venv before trusting "
        "any result from this suite"
    )
