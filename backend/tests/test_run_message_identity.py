"""The rejoin response tells the client which message row the run wrote.

## Why this needs its own file

The header is the answer to a question only the server can settle: *is this run's
answer already in the transcript the client just hydrated?* A replay starts at
frame 0, so a run that completed while the page was closed produces text the
user has never seen rendered -- but the row is in the database, and a client
that also loads history shows it twice.

Nothing about that is derivable client-side. The frames carry no message id, the
count is ambiguous (two runs can legitimately produce the same answer, and one
run can produce none), and comparing text is a guess that breaks on the exact
turn it is meant to protect. So the state travels in a response header, which is
the one channel that crosses the proxy without needing a new SSE frame type --
and an SSE frame type nobody translates is silently dropped (AGENTS.md §2).

The second half of the file covers the message read the client makes first. It
looks like a tidiness change and is not: the previous query returned the
*earliest* window of a conversation, so opening any conversation with more than
`limit` turns showed its first turns and not the ones just asked about -- and the
answer the user had just read was simply not in the payload.
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend.app.api.v1.conversations import MESSAGE_ID_HEADER  # noqa: E402
from backend.app.domain.run.service import RunService  # noqa: E402

from tests.fakes import FakeResult, FakeRunSession, FakeRunStore  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]


# ── The durable store is faked ───────────────────────────────────────────────
# Defined here, shadowing the session-scoped real-database `session_factory` in
# conftest.py on purpose. Nothing under test touches Postgres -- the header is
# built from a row the fake already holds -- and inheriting the conftest fixture
# would open a connection to whatever DATABASE_URL points at, which on this
# machine is hosted Supabase (AGENTS.md §9.16). The fake is the same one
# test_run_rejoin.py and test_batch_a_wiring.py use, from tests/fakes.py,
# because a second copy would be a second answer to what RunRepository issues.


@pytest.fixture
def store() -> FakeRunStore:
    return FakeRunStore()


@pytest.fixture
def session_factory(store: FakeRunStore):
    return lambda: FakeRunSession(store)


# ── the header ──────────────────────────────────────────────────────────────


async def _header_for(session_factory, monkeypatch, *, message_id):
    """GET the rejoin stream for a run and return its response."""
    import backend.app.api.v1.conversations as conv_mod
    from backend.app.api.deps import get_current_user
    from backend.app.main import app
    from httpx import ASGITransport, AsyncClient

    me = uuid4()
    async with session_factory() as session:
        service = RunService(session)
        run = await service.start_run(
            conversation_id=uuid4(), user_id=me, thread_id="t", mode="normal"
        )
        await service.finish(run.id, status="completed", message_id=message_id)

    async def _override():
        return SimpleNamespace(id=me)

    monkeypatch.setattr(conv_mod, "async_session_factory", session_factory)
    app.dependency_overrides[get_current_user] = _override
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get(
                f"/api/v1/conversations/{run.conversation_id}/runs/{run.id}/stream"
            )
    finally:
        app.dependency_overrides.pop(get_current_user, None)
    return resp


async def test_the_rejoin_response_names_the_message_the_run_persisted(
    session_factory, monkeypatch
):
    row_id = uuid4()
    resp = await _header_for(session_factory, monkeypatch, message_id=row_id)

    assert resp.status_code == 200
    # The client compares this against its hydrated history to decide whether to
    # render the replay at all, so a missing or misspelled header here *is* the
    # duplicate answer.
    assert resp.headers.get(MESSAGE_ID_HEADER) == str(row_id)


async def test_a_run_that_wrote_no_row_advertises_no_message(
    session_factory, monkeypatch
):
    """A run still producing, or one that died before persisting, has no id.

    The header's *absence* is the instruction to render the replay -- so it must
    not be synthesised, defaulted, or sent empty. A client reading an empty string
    as an id would match nothing and behave correctly by accident; a client
    reading a placeholder would suppress the answer of every live run.
    """
    resp = await _header_for(session_factory, monkeypatch, message_id=None)

    assert resp.status_code == 200
    assert MESSAGE_ID_HEADER not in resp.headers


def test_the_bff_forwards_the_same_message_id_header_name_the_backend_sets():
    """One value across two repos, and nothing type-checks it.

    A rename on either side leaves the duplicate answer permanently unfixed with
    no failing test anywhere -- the same shape as the run-id header this mirrors.
    """
    bff = (REPO_ROOT / "frontend" / "src" / "app" / "api" / "chat" / "route.ts").read_text(
        encoding="utf-8"
    )
    assert MESSAGE_ID_HEADER == "X-Nexus-Message-Id"
    assert MESSAGE_ID_HEADER.lower() in bff.lower(), (
        f"{MESSAGE_ID_HEADER} is set by the backend but not forwarded by the BFF"
    )


# ── the message read ────────────────────────────────────────────────────────


class _ScriptedSession:
    """A session whose `exec` records instead of running.

    A repository test that mocks the session sees only *that* a query was built,
    not which window it selected -- and the window is the entire behaviour under
    test here.

    ``exec`` is a coroutine because SQLModel's `AsyncSession.exec` is one, and its
    result's ``all()`` is *not*, because SQLModel returns a synchronous
    `ScalarResult` there. Getting that backwards produces "can't be used in
    'await' expression" or "'coroutine' object is not iterable", both of which
    read like a repository bug rather than a broken fake.
    """

    def __init__(self) -> None:
        self.statement: object | None = None

    async def exec(self, statement: object) -> FakeResult:
        self.statement = statement
        return FakeResult([])


async def _sql(**kw) -> str:
    from backend.app.domain.conversation.repository import ConversationRepository

    session = _ScriptedSession()
    await ConversationRepository(session).get_messages(uuid4(), **kw)
    assert session.statement is not None
    compiled = session.statement.compile(compile_kwargs={"literal_binds": False})
    return " ".join(str(compiled).split())


async def _params(**kw) -> dict:
    from backend.app.domain.conversation.repository import ConversationRepository

    session = _ScriptedSession()
    await ConversationRepository(session).get_messages(uuid4(), **kw)
    compiled = session.statement.compile(compile_kwargs={"literal_binds": False})  # type: ignore[union-attr]
    return dict(compiled.params)


async def test_the_newest_window_is_selected_by_a_subquery_not_by_the_order():
    """Which rows come back cannot be decided by an outer `ORDER BY`.

    `ORDER BY created_at DESC LIMIT n` and then sorting ascending in Python is the
    obvious way to write this, and it is wrong on a conversation whose timestamps
    tie -- which they do constantly, because timestamps are naive UTC at
    microsecond resolution written in a tight loop. The ids have to be chosen by
    the database and only then re-ordered for display.
    """
    sql = await _sql(limit=50)

    assert "messages.id IN (SELECT messages.id" in sql, sql
    # The window is a *bounded* select, not a bare membership test.
    assert "LIMIT" in sql.upper(), sql
    # And the window is taken from the tail: descending inside.
    window = sql[sql.index("SELECT messages.id") :]
    assert "ORDER BY messages.created_at DESC" in window, window


async def test_the_window_is_matched_by_membership_not_exclusion():
    """`IN`, not `NOT IN`.

    This is the third axis, and `str(stmt)` alone does not catch it: `IN` and
    `NOT IN` produce the same column list and the same bound parameter, so a
    string assertion on the columns and a params assertion on the value both stay
    green while the query returns exactly the wrong rows (AGENTS.md §9.14). It is
    the one thing in this file that silently produces the *earliest* window with
    every assertion otherwise satisfied.
    """
    sql = await _sql(limit=50)

    assert "NOT IN" not in sql.upper(), sql
    assert "messages.id IN (" in sql, sql


async def test_the_newest_window_still_reads_oldest_first():
    """Descending to choose, ascending to display.

    A window that returns its newest rows newest-first is a different feature:
    the transcript renders in reverse and the answer the user just read appears
    *above* the question that prompted it. The final ordering is asserted
    explicitly -- "the last ORDER BY wins" is the rule the database applies, and
    a test that only counted orderings would not notice them swapped.
    """
    sql = await _sql(limit=50)

    last = sql.upper().rindex("ORDER BY")
    tail = sql[last:]
    assert "DESC" not in tail.upper(), f"the outer ordering is descending: {tail}"
    assert "messages.created_at ASC" in tail, tail


async def test_an_id_tiebreaker_orders_both_sides():
    """`created_at` alone is not a total order.

    Two messages written in the same batch share a `created_at`, and the database
    may return them in either order -- which is how a conversation's last two
    turns swap places on reload, intermittently, and only for long
    conversations. `id` is the stable tiebreak, and it has to appear on *both*
    the window and the outer order: tiebreaking only the window leaves the outer
    sort free to disagree with it about which rows are newest.
    """
    sql = await _sql(limit=50)

    # Counted inside the ORDER BY clauses rather than over the whole statement:
    # `messages.created_at` also appears in the column list, so a whole-SQL
    # count is off by one and a test written against the wrong number fails for
    # a reason nobody can act on.
    assert sql.count("ORDER BY") == 2, sql
    assert "ORDER BY messages.created_at DESC, messages.id DESC" in sql, sql
    assert "ORDER BY messages.created_at ASC, messages.id ASC" in sql, sql


async def test_the_previous_window_is_still_reachable():
    """`newest=False` is not dead code.

    Some questions legitimately want the *head* of a conversation -- branching
    from the opening, an export. Changing the default without keeping the old
    window would have quietly changed those callers too, so both exist, and this
    asserts they are genuinely different queries rather than one ignoring its
    argument.
    """
    earliest = await _sql(limit=50, newest=False)
    newest = await _sql(limit=50, newest=True)

    assert earliest != newest
    # The earliest window is a flat scan: no subquery, and no inner descending.
    assert "IN (SELECT" not in earliest, earliest
    assert "DESC" not in earliest.upper(), earliest
    assert "LIMIT" in earliest.upper(), earliest


async def test_the_limit_is_bound_on_both_windows():
    """A conversation with hundreds of turns must not return all of them.

    The response carries every citation and annotation of every message, so an
    unbounded read is a memory and latency problem that shows up in exactly the
    conversations people care about.
    """
    assert 25 in set((await _params(limit=25)).values())
    assert 25 in set((await _params(limit=25, newest=False)).values())
