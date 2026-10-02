"""Which database is the app about to use, and is the toolchain honest about it?

Two separate failures are covered here, and only one of them is a parser.

  * `.env.example` disagreed with `docker-compose.yml` about user, password and
    database name, so copying the example to `.env` produced a DSN that could
    not reach the container the same file's instructions told you to start.
    Both obvious first-run paths were broken. That is a config-drift bug and the
    first test below is the regression guard for it.

  * `alembic upgrade head` reads DATABASE_URL out of `backend/.env`, which on
    2026-10-01 carried a hosted Supabase DSN, and applied a revision to the live
    database. Nothing objected, because nothing looked. The gate in
    `migrations/env.py` now refuses; the tests for it import the real function
    rather than reimplementing the decision, because a test of a copy of the
    rule proves nothing about the rule.
"""
from __future__ import annotations

import ast
import os
import re
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
REPO = BACKEND.parent

sys.path.insert(0, str(REPO))

from backend.app.core.db_target import (  # noqa: E402
    ALLOW_REMOTE_MIGRATIONS_ENV,
    DatabaseTarget,
    describe_database_target,
    migration_target_refusal,
    remote_migrations_allowed,
)

LOCAL_URL = "postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev"
REMOTE_URL = (
    "postgresql+asyncpg://postgres.abc123:secret@"
    "aws-0-ap-south-1.pooler.supabase.com:6543/postgres"
)


# ── the config drift this change fixes ────────────────────────────────────────


def _compose_postgres_env() -> dict[str, str]:
    """POSTGRES_* from the docker-compose `postgres` service.

    Parsed rather than hardcoded, so the test keeps working if compose is edited
    and keeps failing if the two files drift apart again -- which is the entire
    point.
    """
    text = (REPO / "docker-compose.yml").read_text(encoding="utf-8")
    block = re.search(r"^  postgres:\n(.*?)(?=^  \S|\Z)", text, re.M | re.S)
    assert block, "no `postgres` service in docker-compose.yml"
    found = dict(re.findall(r"POSTGRES_(\w+):\s*(\S+)", block.group(1)))
    assert {"USER", "PASSWORD", "DB"} <= set(found), f"incomplete service: {found}"
    return found


def _example_database_url() -> str:
    text = (BACKEND / ".env.example").read_text(encoding="utf-8")
    match = re.search(r"^DATABASE_URL=(\S+)", text, re.M)
    assert match, "DATABASE_URL is absent from .env.example"
    return match.group(1)


def test_env_example_database_url_reaches_the_compose_database():
    """The headline regression. Copying .env.example to .env must just work.

    Before this fix the example said postgres:postgres@localhost:5432/nexus_db
    while compose created nexus:nexus@localhost:5432/nexus_dev. Nothing detects
    that mismatch at author time -- it surfaces as an authentication or
    "database does not exist" error on a developer's first run, with no hint
    that the file they were told to trust is wrong.
    """
    url = describe_database_target(_example_database_url())
    env = _compose_postgres_env()

    assert url.host in ("localhost", "127.0.0.1"), (
        f".env.example points at {url.host!r}; the first-run database is local"
    )
    assert url.port == "5432", f"compose publishes 5432, example says {url.port!r}"
    assert url.database == env["DB"], (
        f".env.example database {url.database!r} != compose POSTGRES_DB "
        f"{env['DB']!r}; copying the example cannot connect"
    )


def test_env_example_credentials_match_the_compose_service():
    """Username and password, which the DSN target cannot tell us about.

    `describe_database_target` deliberately drops credentials, so this property
    needs its own check rather than riding along on the first test.
    """
    url = _example_database_url()
    env = _compose_postgres_env()
    userinfo = re.match(r"^[a-z+]+://([^:]+):([^@]+)@", url)

    assert userinfo, f"could not parse credentials out of {url!r}"
    assert userinfo.group(1) == env["USER"], (
        f"user {userinfo.group(1)!r} != compose POSTGRES_USER {env['USER']!r}"
    )
    assert userinfo.group(2) == env["PASSWORD"], "password != compose POSTGRES_PASSWORD"


def test_the_code_default_also_matches_compose():
    """The fallback in config.py, which is what you get with no .env at all.

    A third source of truth, and the reason a wrong value can survive: fixing
    the example alone would leave `config.py` disagreeing, and the two are used
    in different situations.
    """
    env = _compose_postgres_env()
    source = (BACKEND / "app" / "core" / "config.py").read_text(encoding="utf-8")
    default = re.search(r'DATABASE_URL:\s*str\s*=\s*Field\(\s*default="([^"]+)"', source)

    assert default, "could not find the DATABASE_URL default in config.py"
    url = describe_database_target(default.group(1))

    assert url.database == env["DB"], (
        f"config.py default database {url.database!r} != compose POSTGRES_DB {env['DB']!r}"
    )
    assert f"//{env['USER']}:{env['PASSWORD']}@" in default.group(1), (
        "config.py default credentials differ from compose"
    )


# ── DSN parsing ──────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("authority", "why"),
    [
        ("localhost", "the obvious one"),
        ("127.0.0.1", "IPv4 loopback"),
        ("0.0.0.0", "a bind address used as a destination; Linux resolves it to loopback"),
        ("[::1]", "IPv6 loopback, bracketed as a DSN requires"),
        ("postgres", "the docker-compose service name"),
        ("db", "a conventional compose service name"),
        ("host.docker.internal", "the host as seen from a container on Docker Desktop"),
    ],
)
def test_every_listed_local_host_really_reads_as_local(authority, why):
    """Each entry in `_LOCAL_HOSTS` is a deliberate inclusion, not a leftover.

    This is the test that justifies the `# nosec B104` on `"0.0.0.0"`. Bandit is
    right that the literal is a bind-all-interfaces address and this is not one;
    the nosec is argued from this test rather than from a preference. Dropping
    the entry to silence the linter would instead make the guard classify a real
    local typo as remote, which is the failure that matters.

    The authority is bracketed for IPv6 because a DSN requires it, and an
    unbracketed `::1` makes the whole netloc unparseable -- which the previous
    test proves is reported as remote rather than local. A wrong fixture there
    would have "proven" the opposite of what it claimed.
    """
    target = describe_database_target(f"postgresql://u:p@{authority}:5432/db")
    assert target.is_local, f"{authority} should read as local ({why})"


@pytest.mark.parametrize(
    "host",
    ["localhost.evil.com", "notlocalhost", "localhost:5432.evil.com", "prod-db"],
)
def test_near_misses_of_localhost_are_not_local(host):
    """Prefix and suffix tricks must not inherit the local verdict.

    `_LOCAL_HOSTS` is matched with `in` against a whole host, not a substring, so
    a lookalike domain cannot borrow localhost's exemption and walk past the
    migration gate.
    """
    assert not describe_database_target(f"postgresql://u:p@{host}/db").is_local


def test_local_dsn_is_recognised_as_local():
    target = describe_database_target(LOCAL_URL)
    assert target.is_local
    assert target.host == "localhost"
    assert target.port == "5432"
    assert target.database == "nexus_dev"


@pytest.mark.parametrize(
    ("url", "why"),
    [
        (REMOTE_URL, "the hosted pooler"),
        ("postgresql://user:pw@db.example.com:5432/prod", "a plain remote host"),
        ("postgresql://user:pw@10.0.0.5/prod", "a private-network address"),
        ("postgresql://user:pw@prod-db.internal/prod", "an internal hostname"),
    ],
)
def test_remote_dsns_are_not_local(url, why):
    """Every one of these changes a live database and must not read as local."""
    assert not describe_database_target(url).is_local, why


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user@localhost/db",  # no port
        "postgresql://localhost:5432/db",  # no credentials
        "postgresql://[::1]:5432/db",  # IPv6 literal
        "postgresql:///nexus_dev",  # no authority: unix socket
        "  postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev  ",  # padded
        "POSTGRESQL://nexus:nexus@LOCALHOST:5432/nexus_dev",  # upper-case scheme+host
    ],
)
def test_local_shapes_all_parse_as_local(url):
    assert describe_database_target(url).is_local, url


def test_unparseable_and_empty_never_raise():
    """The boot-time caller must not be able to crash on a malformed DSN.

    `describe_database_target` is called from `main.py`'s lifespan before
    anything else useful happens. A parser that raised here would take down a
    process that could otherwise serve every request.
    """
    for bad in ("", "   ", None, "not-a-dsn", "://///", "postgres://host:notaport/db"):
        target = describe_database_target(bad)  # type: ignore[arg-type]
        assert isinstance(target, DatabaseTarget)
        assert target.host  # never an empty string: a blank log line hides the failure


def test_a_port_that_is_not_a_number_is_not_trusted_as_local():
    """A malformed port means the DSN is malformed, so it is not called local.

    `urlsplit("postgresql://u:p@localhost:5432.evil.com/db").hostname` still
    returns `localhost`, while `.port` raises. Catching the error and keeping the
    host would report this as a confident local target and let it past the
    migration gate -- a guard that grants an exemption it did not earn.
    """
    target = describe_database_target("postgres://host:notaport/db")
    assert not target.is_local, "a DSN whose port cannot be read must not be local"

    lookalike = describe_database_target("postgresql://u:p@localhost:5432.evil.com/db")
    assert not lookalike.is_local, (
        "a lookalike authority inherited localhost's exemption; "
        f"got {lookalike!r}"
    )


def test_malformed_port_does_not_raise():
    """The boot-time caller must still not crash on it."""
    describe_database_target("postgres://host:notaport/db")  # must not raise


def test_log_kwargs_never_leak_the_password():
    """The parsed target is logged on every boot, so it must hold no secret."""
    target = describe_database_target(REMOTE_URL)
    blob = repr(target) + repr(target.as_log_kwargs()) + target.describe()

    assert "secret" not in blob, "a credential reached a loggable representation"
    assert "postgres.abc123" not in blob


def test_describe_omits_absent_parts():
    assert describe_database_target("postgresql://localhost").describe() == "localhost"
    assert describe_database_target(LOCAL_URL).describe() == "localhost:5432/nexus_dev"


# ── the migration gate's decision, not a copy of it ──────────────────────────


class _ContextStub:
    """Stands in for Alembic's `context` while the gate is lifted out of env.py.

    The gate reads `context.is_offline_mode()` and nothing else from the
    framework, so one flag is the whole seam. Injecting this is what makes the
    real check testable -- an `offline=` keyword on the gate would not, because
    a parameter that overrides the value under test is a second source of truth
    for it, and the revert harness proved that one went untested.
    """

    def __init__(self, offline: bool) -> None:
        self._offline = offline

    def is_offline_mode(self) -> bool:
        return self._offline


def _load_gate(*, offline: bool = False) -> dict[str, object]:
    """Compile the real gate out of `migrations/env.py` and return its namespace.

    Importing env.py normally would execute the migration dispatch at module
    scope, so the function is lifted out of the source and compiled alone. That
    is still the shipped source text -- a test of a reimplementation would pass
    while the gate did something else, which is the exact failure mode this
    repository keeps hitting.

    Defaults to online mode because that is the mode that touches a database; a
    default of offline would silently exempt every gate test.
    """
    source = (BACKEND / "migrations" / "env.py").read_text(encoding="utf-8")
    start = source.index("MUTATING_COMMANDS = frozenset")
    end = source.index("PGBOUNCER_SAFE_CONNECT_ARGS")
    namespace: dict[str, object] = {
        "sys": sys,
        "os": os,
        "context": _ContextStub(offline),
        # The slice starts below the import block, so the names env.py imports
        # have to be supplied here -- including the opt-in variable, which the
        # warning interpolates.
        "ALLOW_REMOTE_MIGRATIONS_ENV": ALLOW_REMOTE_MIGRATIONS_ENV,
        "describe_database_target": describe_database_target,
        "migration_target_refusal": migration_target_refusal,
        "remote_migrations_allowed": remote_migrations_allowed,
    }
    exec(compile(source[start:end], "env.py", "exec"), namespace)  # noqa: S102
    return namespace


def test_gate_refuses_upgrade_against_a_remote_database(monkeypatch):
    """The incident itself. Must raise, not warn."""
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["alembic", "upgrade", "head"])
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    with pytest.raises(RuntimeError) as excinfo:
        gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]

    message = str(excinfo.value)
    assert "aws-0-ap-south-1.pooler.supabase.com" in message, (
        "the refusal must name the target, or the reader cannot tell whether it fired"
    )
    assert ALLOW_REMOTE_MIGRATIONS_ENV in message, "the refusal must name the opt-in"


@pytest.mark.parametrize("command", ["upgrade", "downgrade"])
def test_gate_refuses_downgrade_too(monkeypatch, command):
    """A downgrade drops schema. It is at least as dangerous as an upgrade."""
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["alembic", command, "-1"])
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    with pytest.raises(RuntimeError):
        gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]


def test_gate_allows_a_local_database_with_no_opt_in(monkeypatch):
    """The common case must not need ceremony."""
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["alembic", "upgrade", "head"])
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    gate["assert_migration_target_allowed"](LOCAL_URL)  # type: ignore[operator]


def test_opt_in_lets_a_deliberate_remote_migration_through(monkeypatch, capsys):
    """Allowed through, but it must SAY that it is touching a remote database.

    The first version of this warning interpolated the literal string
    'ALLOW_REMOTE_MIGRATIONS_ENV' instead of the constant, so it printed
    `because None is set` -- the one line telling the operator their opt-in was
    recognised told them nothing. Found by running real alembic, not by any unit
    test, which is the argument for having run it.
    """
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["alembic", "upgrade", "head"])
    monkeypatch.setenv(ALLOW_REMOTE_MIGRATIONS_ENV, "1")

    gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]

    warning = capsys.readouterr().err
    assert ALLOW_REMOTE_MIGRATIONS_ENV in warning, (
        f"the warning must name the variable that was set; got {warning!r}"
    )
    assert "aws-0-ap-south-1.pooler.supabase.com" in warning, (
        "the warning must name the database being migrated"
    )
    assert "None" not in warning, "the warning is reporting a value it did not read"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on"])
def test_every_affirmative_spelling_is_accepted(monkeypatch, value):
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", ["alembic", "upgrade", "head"])
    monkeypatch.setenv(ALLOW_REMOTE_MIGRATIONS_ENV, value)

    gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]


@pytest.mark.parametrize("value", ["", "0", "false", "no", "maybe", " "])
def test_a_non_affirmative_opt_in_does_not_count(monkeypatch, value):
    """`ALLOW_REMOTE_MIGRATIONS=0` must not read as permission."""
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)
    monkeypatch.setenv(ALLOW_REMOTE_MIGRATIONS_ENV, value)
    assert not remote_migrations_allowed()


@pytest.mark.parametrize(
    "argv",
    [
        ["alembic", "revision", "--autogenerate", "-m", "add users"],
        ["alembic", "history", "--verbose"],
        ["alembic", "current"],
        ["alembic", "heads"],
    ],
)
def test_read_only_commands_are_not_gated(monkeypatch, argv):
    """Inspecting a remote database is legitimate and must not need the opt-in.

    Gating read-only commands would not add safety -- it would only teach people
    to set the opt-in once and then leave it set.
    """
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]


@pytest.mark.parametrize(
    "argv",
    [
        ["alembic", "upgrade", "head", "--sql"],
        ["alembic", "downgrade", "base", "--sql"],
    ],
)
def test_offline_sql_generation_is_not_gated(monkeypatch, argv):
    """`--sql` prints statements and never connects, so there is nothing to protect.

    This is the standard way to review a migration before running it. Blocking
    the preview in order to protect the database blocks the one action that
    reduces risk, and is the kind of friction that makes people run the real
    command instead.

    The exemption is driven through the injected Alembic context, not through an
    argument on the gate, so this exercises the line that actually reads the
    framework's state. Deleting that line outright leaves this test failing --
    which is the whole point, and is why the earlier `offline=` keyword is gone.
    """
    gate = _load_gate(offline=True)
    monkeypatch.setattr(sys, "argv", argv)
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    gate["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]


def test_online_mode_is_still_gated_for_the_same_argv(monkeypatch):
    """Same command, same URL, same environment -- only the mode differs.

    Without this, a gate that ignored the mode entirely (never exempting offline,
    or exempting everything) would satisfy the test above. The pair pins that
    `is_offline_mode()` is what decides.
    """
    monkeypatch.setattr(sys, "argv", ["alembic", "upgrade", "head", "--sql"])
    monkeypatch.delenv(ALLOW_REMOTE_MIGRATIONS_ENV, raising=False)

    with pytest.raises(RuntimeError):
        _load_gate(offline=False)["assert_migration_target_allowed"](REMOTE_URL)  # type: ignore[operator]


def test_the_offline_exemption_comes_only_from_the_alembic_mode():
    """No override parameter exists, so nothing can bypass the mode by argument.

    Guards the property the revert harness found: once a test can pass the flag
    itself, the framework's own signal stops being covered.

    Parsed with `ast`, and that is not incidental. The first version read the
    signature with `source.split("def ...", 1)[1].split(":", 1)[0]`, which stops
    at the colon of `(url: str` and yields the literal `"(url"` -- so the
    substring could never be present and the assertion could never fail. It
    reported green against the exact edit it was written to catch. A source-shape
    claim needs the parser, not a split on a delimiter that also occurs inside
    the thing being measured.
    """
    tree = ast.parse((BACKEND / "migrations" / "env.py").read_text(encoding="utf-8"))
    gate = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "assert_migration_target_allowed"
    )
    params = [arg.arg for arg in (*gate.args.args, *gate.args.kwonlyargs)]

    assert params == ["url"], (
        f"the gate should take only the DSN; got {params}. An override lets a "
        f"caller (including a test) decide for Alembic whether it is offline."
    )
    assert gate.args.kwarg is None and gate.args.vararg is None, (
        "**kwargs or *args would re-open the same hole"
    )


@pytest.mark.parametrize(
    ("argv", "expected", "why"),
    [
        (["alembic", "upgrade", "head"], "upgrade", "the plain case"),
        (["alembic", "downgrade", "-1"], "downgrade", "rollback"),
        (
            ["alembic", "-x", "upgrade=1", "upgrade", "head"],
            "upgrade",
            "a bare verb after a flag still counts",
        ),
        (
            ["alembic", "revision", "-m", "upgrade the users table"],
            None,
            "the word appearing inside a message must not count",
        ),
        (["alembic", "upgrade-head"], None, "a near-miss verb is not the verb"),
    ],
)
def test_only_a_bare_verb_counts_as_the_command(monkeypatch, argv, expected, why):
    """argv is a blunt instrument, so the exactness of the match is pinned.

    A substring or prefix match would fire on `upgrade=1` and on a revision
    message containing the word, and a guard that fires on harmless input gets
    disabled.
    """
    gate = _load_gate()
    monkeypatch.setattr(sys, "argv", argv)

    assert gate["_requested_command"]() == expected, why  # type: ignore[operator]


def test_the_gate_is_wired_into_both_migration_paths():
    """Both entry points, because a guard on only one is not a guard.

    Offline (`--sql`) and online both change a schema. Checking the call count in
    the source rather than the behaviour: driving Alembic for real would need a
    database, and a source assertion here is honest about that limit -- it
    proves the call is present, not that it fires, which the argv tests above
    cover.
    """
    source = (BACKEND / "migrations" / "env.py").read_text(encoding="utf-8")
    assert source.count("assert_migration_target_allowed(url)") == 2, (
        "expected the gate in run_migrations_offline and run_async_migrations"
    )


def test_main_logs_the_target_before_it_writes():
    """`init_db()` runs create_all, so the log has to come first, not after.

    Asserting on source order rather than behaviour: exercising the lifespan
    needs a live Redis, Qdrant and database. This pins the property that
    actually matters -- the reader learns the target before the write, not
    after -- and the parser and gate are covered behaviourally above.
    """
    source = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")
    logged = source.index("database_target_resolved")
    written = source.index("await init_db()")

    assert logged < written, "the target is logged after init_db() already wrote to it"


def test_the_remote_warning_has_a_distinct_name_and_a_real_guard():
    """The event name is matched with its closing quote, not as a prefix.

    The first version of this asserted `"database_target_is_not_local" in source`
    and the revert harness renamed the event to
    `"database_target_is_not_local_removed"` -- which still contains the original
    as a substring, so the suite stayed green while the operator's warning had
    been effectively deleted. A substring search cannot distinguish an identifier
    from a longer identifier that starts with it; the surrounding quotes can.

    The `if not ... is_local` guard is asserted separately because renaming the
    event is only half the deletion: an unconditional `logger.warning` would
    still pass a name-only check while warning about localhost.
    """
    source = (BACKEND / "app" / "main.py").read_text(encoding="utf-8")

    assert '"database_target_is_not_local"' in source, (
        "the remote-target warning event is missing or renamed"
    )
    assert "if not _db_target.is_local:" in source, (
        "the remote warning is not guarded by the locality check"
    )
    assert '"database_target_resolved"' in source, (
        "the always-on target log is missing; this is the line that proves the "
        "local case too"
    )
