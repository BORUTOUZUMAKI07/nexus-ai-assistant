"""Structural guards on the Alembic revision graph.

There was no test here, and the graph was broken. `a7b8c9d0e1f2` (row-level
security) and `b1c2d3e4f5a6` (memory lifecycle) were both written against
`f6a7b8c9d0e1` and committed independently, leaving two heads. Alembic refuses
to resolve `head` when there is more than one, so `alembic upgrade head` died
with "Multiple head revisions are present" before running a statement — which is
the documented first-boot command and what `make migrate` invokes.

Nothing caught it because nothing in the suite touches `migrations/`, and
because a branched graph is not an error at import time. Both revisions had to
be reached by naming them explicitly, which is not a step anyone takes.

The checks here are cheap and structural on purpose: they read the revision
graph, not a database, so they run in the unit suite with no Postgres.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent
VERSIONS = BACKEND / "migrations" / "versions"
ALEMBIC_INI = BACKEND / "alembic.ini"

sys.path.insert(0, str(REPO))


def _revision_files() -> list[Path]:
    files = sorted(VERSIONS.glob("*.py"))
    assert files, f"no revisions found under {VERSIONS}"
    return files


def _graph() -> dict[str, str | None]:
    """revision id -> down_revision, read with `ast` rather than by importing.

    Importing a revision executes its module body, which imports application
    models and needs a database URL. Parsing instead keeps this in the unit
    suite and means a revision with a genuine import error is still counted in
    the graph check rather than crashing collection.
    """
    graph: dict[str, str | None] = {}
    for path in _revision_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: dict[str, str | None] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign):
                continue
            target = node.target
            if not isinstance(target, ast.Name):
                continue
            if target.id not in ("revision", "down_revision"):
                continue
            value = node.value
            if isinstance(value, ast.Constant):
                found[target.id] = value.value
            elif value is None or (
                isinstance(value, ast.Constant) and value.value is None
            ):
                found[target.id] = None
        assert "revision" in found, f"{path.name} has no `revision` annotation"
        assert "down_revision" in found, (
            f"{path.name} has no `down_revision` annotation -- Alembic requires "
            "it to be declared even when the value is None"
        )
        graph[found["revision"]] = found["down_revision"]
    return graph


def test_the_revision_graph_has_exactly_one_head() -> None:
    """The invariant that was violated, and that breaks `upgrade head`."""
    graph = _graph()
    parents = {parent for parent in graph.values() if parent is not None}

    unknown = parents - graph.keys()
    assert not unknown, (
        f"revisions point at down_revisions that do not exist: {sorted(unknown)}"
    )

    heads = sorted(set(graph) - parents)
    assert len(heads) == 1, (
        f"expected 1 head, found {len(heads)}: {heads}. Alembic cannot resolve "
        "`head` with more than one, so `alembic upgrade head` and `make "
        "migrate` fail before executing anything."
    )


def test_the_revision_graph_is_fully_connected() -> None:
    """Every revision is reachable from the single head by walking down.

    A second head is the visible failure; this catches the quieter one where a
    revision is orphaned -- declared, never referenced, silently never applied.
    """
    graph = _graph()
    parents = {parent for parent in graph.values() if parent is not None}
    heads = sorted(set(graph) - parents)
    assert len(heads) == 1, f"expected 1 head, found {heads}"

    reached: set[str] = set()
    cursor: str | None = heads[0]
    while cursor is not None:
        assert cursor not in reached, f"cycle in the revision graph at {cursor}"
        reached.add(cursor)
        cursor = graph[cursor]

    orphans = sorted(set(graph) - reached)
    assert not orphans, (
        f"revisions unreachable from head {heads[0]}: {orphans}. They would "
        "never be applied by `upgrade head`."
    )


def test_exactly_one_revision_starts_the_chain() -> None:
    graph = _graph()
    bases = sorted(rev for rev, down in graph.items() if down is None)
    assert len(bases) == 1, f"expected exactly one base revision, found {bases}"


@pytest.mark.parametrize(
    ("key", "expected"),
    [("script_location", "migrations"), ("prepend_sys_path", "..")],
)
def test_alembic_ini_paths(key: str, expected: str) -> None:
    """`prepend_sys_path` must be the repo root, not `backend/`.

    Every revision imports application metadata as
    `from backend.app.domain...`, so autogenerate compares against the same
    metadata the running app uses. With `.` here, meaning `backend/`, the path
    gains `backend.app` and every revision fails at import with
    `No module named 'backend'` -- before Alembic inspects a statement, and
    identically for all nine revisions, so nothing distinguishes them.

    Both settings resolve against the CURRENT WORKING DIRECTORY rather than
    against this ini file, which is why they are only correct when alembic runs
    from `backend/`.
    """
    text = ALEMBIC_INI.read_text(encoding="utf-8")
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip().startswith(f"{key}")
    ]
    assert lines, f"alembic.ini declares no {key}"
    for line in lines:
        name, _, value = line.partition("=")
        assert name.strip() == key, f"unexpected line while reading {key}: {line}"
        assert value.strip() == expected, (
            f"alembic.ini {key} is {value.strip()!r}, expected {expected!r}"
        )


def test_rls_revision_is_reachable_from_head() -> None:
    """The revision that closes the two Supabase advisories must be applied.

    Worth its own assertion rather than folding it into the graph tests: this
    one has a named purpose, and a future revision that reintroduces a branch
    should fail here with that context instead of as an anonymous head count.
    """
    graph = _graph()
    assert "a7b8c9d0e1f2" in graph, "the row-level-security revision is gone"

    parents = {parent for parent in graph.values() if parent is not None}
    heads = sorted(set(graph) - parents)
    assert len(heads) == 1, f"expected 1 head, found {heads}"

    reached: set[str] = set()
    cursor: str | None = heads[0]
    while cursor is not None and cursor not in reached:
        reached.add(cursor)
        cursor = graph[cursor]

    assert "a7b8c9d0e1f2" in reached, (
        "the row-level-security revision is not reachable from head, so "
        "`rls_disabled_in_public` and `sensitive_columns_exposed` would still "
        "be reported by Supabase Shield"
    )


def _declared_unique_constraints() -> dict[str, str]:
    """Named ``UniqueConstraint`` in the models -> the file that declares it.

    Read with `ast` rather than by importing: importing builds the SQLModel
    metadata and needs a database URL, which would keep this out of the unit
    suite (same reasoning as `_graph`).
    """
    declared: dict[str, str] = {}
    for path in sorted((BACKEND / "app").rglob("models.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                called = func.attr
            elif isinstance(func, ast.Name):
                called = func.id
            else:
                continue
            if not called.endswith("UniqueConstraint"):
                continue
            for kw in node.keywords:
                if (
                    kw.arg == "name"
                    and isinstance(kw.value, ast.Constant)
                    and isinstance(kw.value.value, str)
                ):
                    declared[kw.value.value] = str(path.relative_to(REPO))
    return declared


def _migration_string_constants() -> set[str]:
    """String literals used in migration *code*, docstrings excluded.

    Docstrings are excluded deliberately: `c2a1b2c3d4e5` names
    `uq_run_events_run_id_seq` in its module docstring *and* creates it, so a
    naive substring scan would keep passing after the `create_unique_constraint`
    call was deleted -- the "passes for the wrong reason" shape this repo keeps
    finding.
    """
    constants: set[str] = set()
    for path in _revision_files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(
                node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                body = getattr(node, "body", [])
                if (
                    body
                    and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)
                ):
                    docstrings.add(id(body[0].value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                constants.add(node.value)
    return constants


def test_every_declared_unique_constraint_is_created_by_a_migration() -> None:
    """A constraint the models declare but no revision creates is invisible.

    ``create_all`` at startup only builds tables that do not exist, so adding a
    ``UniqueConstraint`` to a model changes nothing on any database that already
    has the table. The org-member and api-key constraints were added in
    ``domain/org/models.py`` and ``domain/user/models.py`` with no revision,
    which would have left them absent in production while a fresh test database
    -- built by ``create_all`` from the current models -- had them, so no test
    could have noticed.
    """
    declared = _declared_unique_constraints()
    assert declared, "no named UniqueConstraint found under app/**/models.py"

    constants = _migration_string_constants()
    missing = {
        name: origin for name, origin in declared.items() if name not in constants
    }
    assert not missing, (
        f"UniqueConstraint(s) declared in models but created by no revision: "
        f"{missing}. `create_all` does not alter existing tables, so these would "
        "be missing on any database that already has the table. Add a revision "
        "under backend/migrations/versions/ that creates them."
    )
