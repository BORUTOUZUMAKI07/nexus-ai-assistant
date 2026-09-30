"""
Tests for the checkpointer DSN resolution in orchestrator/graph.py.

Why the port rewrite needs tests
───────────────────────────────
``_resolve_checkpoint_dsn`` silently changes the port of ``DATABASE_URL`` when it
finds a transaction-mode pooler port. That is a deliberate behaviour with a
real failure behind it, and it is exactly the kind of change that rots: a
future refactor of the URL parsing could quietly stop matching, and the only
symptom would be the production traceback returning weeks later.

So both directions are pinned here — the rewrite happens for a pooler, and does
*not* happen for anything else. The negative cases matter more than the positive
one; a resolver that rewrote every port would pass a single happy-path test.
"""
from __future__ import annotations

import pytest
from backend.app.agents.orchestrator import graph as graph_mod
from backend.app.agents.orchestrator.graph import _resolve_checkpoint_dsn
from backend.app.core.redaction import redact_dsn


@pytest.fixture
def with_settings(monkeypatch):
    def _apply(database_url: str, explicit: str | None = None):
        monkeypatch.setattr(graph_mod.settings, "DATABASE_URL", database_url, raising=False)
        monkeypatch.setattr(
            graph_mod.settings, "LANGGRAPH_CHECKPOINT_DSN", explicit, raising=False
        )
        # Guard against os.environ leaking in via the fallback branch.
        monkeypatch.delenv("DATABASE_URL", raising=False)

    return _apply


# ── the fix: a transaction-mode pooler port is upgraded ─────────────────────


def test_upgrades_the_supabase_transaction_pooler_port(with_settings):
    # The exact shape that produced the incident: port 6543 is pgbouncer in
    # transaction mode, which may close a long-lived connection at will.
    with_settings("postgresql+asyncpg://postgres.ref:pw@aws-0-x.pooler.supabase.com:6543/postgres")

    dsn = _resolve_checkpoint_dsn()

    assert ":6543/" not in dsn
    assert ":5432/" in dsn
    # Host, credentials and database are untouched -- only the port moves.
    assert "aws-0-x.pooler.supabase.com" in dsn
    assert "postgres.ref" in dsn
    assert dsn.endswith("/postgres")


def test_upgraded_dsn_is_still_psycopg3_not_asyncpg(with_settings):
    # AsyncConnection cannot parse the SQLAlchemy "+asyncpg" driver suffix, so
    # the conversion has to survive the port rewrite.
    with_settings("postgresql+asyncpg://u:p@h:6543/db")

    dsn = _resolve_checkpoint_dsn()

    assert not dsn.startswith("postgresql+asyncpg://")
    assert dsn.startswith("postgresql://")


# ── explicit override wins over everything ─────────────────────────────────


def test_explicit_dsn_is_used_verbatim(with_settings):
    # An operator who knows better than the heuristic (a dedicated reader
    # replica, a local Postgres) must be able to say so.
    with_settings(
        "postgresql+asyncpg://u:p@pooler:6543/db",
        explicit="postgresql+asyncpg://u:p@replica.internal:5432/db",
    )

    dsn = _resolve_checkpoint_dsn()

    # Used verbatim, including the driver suffix being normalised for psycopg
    # but NOT the port being touched.
    assert dsn == "postgresql://u:p@replica.internal:5432/db"


def test_explicit_dsn_can_point_at_the_pooler_on_purpose(with_settings):
    # The escape hatch has to work in both directions, otherwise there is no
    # way to opt out of the rewrite at all.
    with_settings(
        "postgresql+asyncpg://u:p@h:5432/db",
        explicit="postgresql+asyncpg://u:p@h:6543/db",
    )

    assert ":6543/" in _resolve_checkpoint_dsn()


# ── negative cases: nothing else may be rewritten ──────────────────────────


def test_leaves_a_normal_port_alone(with_settings):
    with_settings("postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev")

    dsn = _resolve_checkpoint_dsn()

    assert dsn == "postgresql://nexus:nexus@localhost:5432/nexus_dev"


def test_leaves_a_bare_localhost_url_alone(with_settings):
    with_settings("postgresql://nexus:nexus@localhost:5432/nexus_dev")

    assert _resolve_checkpoint_dsn() == "postgresql://nexus:nexus@localhost:5432/nexus_dev"


def test_does_not_rewrite_6543_appearing_somewhere_other_than_the_port(with_settings):
    # A password or database name containing "6543" must not trigger the
    # rewrite. A naive `str.replace("6543", "5432")` would corrupt it.
    with_settings("postgresql+asyncpg://u:6543secret@h:5432/db6543")

    dsn = _resolve_checkpoint_dsn()

    assert "6543secret" in dsn
    assert "db6543" in dsn
    assert ":5432/" in dsn


def test_does_not_rewrite_an_unrelated_port_6544(with_settings):
    with_settings("postgresql+asyncpg://u:p@h:6544/db")

    assert ":6544/" in _resolve_checkpoint_dsn()


def test_falls_back_to_a_local_default_when_no_url_is_set(with_settings):
    # settings.DATABASE_URL is typed `str` and always populated in practice, but
    # the code has a fallback and it must not raise.
    with_settings("")

    dsn = _resolve_checkpoint_dsn()

    assert dsn.startswith("postgresql://")
    assert "localhost" in dsn


# ── the DSN that gets logged must not carry the password ───────────────────


def test_logged_dsn_is_redacted_not_truncated():
    # `lifespan_graph` logs the DSN when the checkpointer initialises, which is
    # exactly the moment someone needs it. Truncating to 40 chars was the
    # previous approach and it is not a redaction: for a Supabase pooler URL it
    # is safe only because `postgres.<16-char-ref>` is exactly 25 characters,
    # so the cut lands on the colon. A shorter username, or the `+asyncpg`
    # driver suffix, prints the password in full.
    dsn = "postgresql://u:MyRealPassword123@aws-0-x.pooler.supabase.com:6543/postgres"

    logged = redact_dsn(dsn)

    assert "MyRealPassword123" not in logged
    # Still diagnosable: role, host, port and database all survive.
    assert "://u:" in logged
    assert "aws-0-x.pooler.supabase.com" in logged
    assert ":6543/" in logged
    assert logged.endswith("/postgres")


def test_redaction_keeps_the_supabase_role_visible():
    # The username identifies the Supabase project ref, which is the thing you
    # actually need when debugging pooler auth. Only the password goes.
    dsn = "postgresql://postgres.huemplbhcgalykovkanh:pw@pooler.supabase.com:5432/postgres"

    logged = redact_dsn(dsn)

    assert "postgres.huemplbhcgalykovkanh" in logged
    assert "pw@" not in logged
    assert ":***@" in logged


def test_redaction_handles_the_asyncpg_driver_suffix(with_settings):
    with_settings("postgresql+asyncpg://u:pw@h:5432/db")

    dsn = _resolve_checkpoint_dsn()
    logged = redact_dsn(dsn)

    assert "pw" not in logged.split("@")[0].split(":")[-1]
    assert logged.startswith("postgresql://")


def test_redaction_is_a_noop_without_credentials():
    # A DSN with no password (trust auth, socket dir) must come back unchanged
    # rather than gaining a spurious ":***@".
    dsn = "postgresql://postgres@/var/run/postgresql/db"

    assert redact_dsn(dsn) == dsn


def test_redaction_handles_an_empty_string():
    assert redact_dsn("") == ""
