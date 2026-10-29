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
