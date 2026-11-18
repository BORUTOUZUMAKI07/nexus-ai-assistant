"""Structural checks on the durable post-answer outbox revision.

The migration is source-only in this environment: no hosted database is
reachable, so these checks prove the DDL is present, idempotent, wired into the
single Alembic head, and forward-only without connecting anywhere.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

from backend.app.services.outbox.models import PostAnswerTask

BACKEND = Path(__file__).resolve().parents[2]
VERSIONS = BACKEND / "migrations" / "versions"
REVISION = "f9a0b1c2d3e4"
MIGRATION = next(VERSIONS.glob(f"{REVISION}_*.py"), None)


def _graph() -> dict[str, str | None]:
    graph: dict[str, str | None] = {}
    for path in VERSIONS.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        found: dict[str, str | None] = {}
        for node in ast.walk(tree):
            if not isinstance(node, ast.AnnAssign) or not isinstance(node.target, ast.Name):
                continue
            if node.target.id in ("revision", "down_revision") and isinstance(
                node.value, ast.Constant
            ):
                found[node.target.id] = node.value.value
        graph[found["revision"]] = found["down_revision"]
    return graph


def _module():
    assert MIGRATION is not None, f"missing revision file for {REVISION}"
    spec = importlib.util.spec_from_file_location("post_answer_outbox_migration", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_outbox_revision_is_the_single_head_after_the_integrity_repair():
    graph = _graph()
    parents = {parent for parent in graph.values() if parent is not None}
    heads = sorted(set(graph) - parents)
    assert heads == [REVISION]
    assert graph[REVISION] == "e7f8a9b0c1d2"


def test_migration_creates_the_approved_idempotent_ddl():
    assert MIGRATION is not None, f"missing revision file for {REVISION}"
    source = MIGRATION.read_text(encoding="utf-8")
    tree = ast.parse(source)
    # Adjacent literals, the f-string pieces of CREATE TABLE and the named
    # constraint constant form one SQL statement at execution time; compare the
    # normalised text rather than raw source slices.
    sql = " ".join(
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    )
    normalized = " ".join(sql.split())
    expected = (
        "CREATE TABLE IF NOT EXISTS post_answer_tasks (",
        "id UUID PRIMARY KEY,",
        "run_id UUID NOT NULL REFERENCES agent_runs(id) ON DELETE CASCADE,",
        "message_id UUID,",
        "task_type VARCHAR NOT NULL,",
        "idempotency_key VARCHAR NOT NULL,",
        "payload JSONB NOT NULL DEFAULT '{}'::jsonb,",
        "status VARCHAR NOT NULL DEFAULT 'pending',",
        "attempts INTEGER NOT NULL DEFAULT 0,",
        "max_attempts INTEGER NOT NULL DEFAULT 5,",
        "available_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),",
        "locked_at TIMESTAMP WITHOUT TIME ZONE,",
        "last_error TEXT,",
        "created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (now() AT TIME ZONE 'utc'),",
        "finished_at TIMESTAMP WITHOUT TIME ZONE,",
        "CONSTRAINT",
        "uq_post_answer_tasks_idempotency",
        "UNIQUE (idempotency_key)",
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_id ON post_answer_tasks (id)",
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_run_id ON post_answer_tasks (run_id)",
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_status ON post_answer_tasks (status)",
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_available_at ON post_answer_tasks (available_at)",
        "CREATE INDEX IF NOT EXISTS ix_post_answer_tasks_message_id ON post_answer_tasks (message_id)",
    )
    for fragment in expected:
        assert " ".join(fragment.split()) in normalized, f"missing DDL fragment: {fragment}"
    # RLS is applied by the a7b8c9d0e1f2 ddl_command_end event trigger.
    assert "ROW LEVEL SECURITY" not in source
    assert "CREATE POLICY" not in source


def test_migration_index_names_match_the_model_metadata():
    model_indexes = {index.name for index in PostAnswerTask.__table__.indexes}
    assert model_indexes == {
        "ix_post_answer_tasks_id",
        "ix_post_answer_tasks_run_id",
        "ix_post_answer_tasks_status",
        "ix_post_answer_tasks_available_at",
        "ix_post_answer_tasks_message_id",
    }


def test_downgrade_is_a_no_op_and_never_drops_queued_work():
    module = _module()
    tree = ast.parse(MIGRATION.read_text(encoding="utf-8")) if MIGRATION else None
    assert tree is not None
    downgrade = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
    )
    assert not [node for node in ast.walk(downgrade) if isinstance(node, ast.Call)]
    # Calling it outside an Alembic context would raise if it touched op.
    module.downgrade()
