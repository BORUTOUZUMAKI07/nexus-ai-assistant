"""Identify which database a DSN points at, and whether it is local.

Pure string parsing. No I/O, no settings import, no app imports.

Two callers with opposite failure requirements:

  * ``app/main.py`` logs the target on every boot and must NEVER raise --
    booting is not optional, and one unparseable DSN must not stop the process
    serving everything else.
  * ``migrations/env.py`` refuses to run ``upgrade``/``downgrade`` against a
    remote database and MUST raise.

So the parser never raises and never returns None: an unparseable DSN degrades
to a local, obviously-labelled target. That is the safe direction for the
boot-time caller. For the migration gate it is the *unsafe* direction -- an
unparseable DSN would be allowed through -- which is why ``env.py`` treats a
non-local or unlabelled host as remote and refuses. ``is_local`` is only True
for a host that positively matches the local set.

Why this exists at all: on 2026-10-01 an ``alembic upgrade head`` intended for
the local container read ``backend/.env``, which carried a hosted Supabase
DATABASE_URL, and applied a revision to the live database. Nothing in the tool
chain objected, because nothing in the tool chain looked. The command that
changed a database was allowed to pick its own target silently.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

# Hostnames that mean "this machine" or "the compose network". Matched
# case-insensitively against the lowercased parsed host.
#
# `postgres` and `db` are the docker-compose service names: from inside the
# compose network the local database answers to those, not to `localhost`.
# `host.docker.internal` is the host as seen from a container on Docker Desktop.
#
# This is a guard against accidents, not a security boundary -- a remote host
# that happened to be named `db` would be misclassified. That is acceptable
# because the consequence is a warning that is wrong, not a bypass that is
# quiet; the opt-in below is still required for any host not on this list.
_LOCAL_HOSTS = frozenset(
    {
        "localhost",
        "127.0.0.1",
        # `0.0.0.0` is a *bind* address and belongs on no client, but as a
        # destination Linux resolves it to localhost -- so a DATABASE_URL written
        # with it means "I meant local", and treating it as local is the correct
        # and safe reading. Bandit's B104 sees only the literal; nosec with the
        # reason rather than dropping the entry, because dropping it would make
        # this guard classify a real local typo as remote.
        "0.0.0.0",  # nosec B104
        "::1",
        "[::1]",
        "postgres",
        "db",
        "host.docker.internal",
    }
)

# Values accepted as affirmative for the migration opt-in.
_TRUTHY = frozenset({"1", "true", "yes", "on"})

# The environment variable that authorizes migrating a non-local database.
ALLOW_REMOTE_MIGRATIONS_ENV = "ALLOW_REMOTE_MIGRATIONS"


@dataclass(frozen=True)
class DatabaseTarget:
    """Where a DSN points. `host` is lowercased and never contains a secret."""

    host: str
    port: str | None
    database: str | None
    is_local: bool

    def describe(self) -> str:
        """`host:port/database`, omitting whichever part is absent."""
        authority = f"{self.host}:{self.port}" if self.port else self.host
        return f"{authority}/{self.database}" if self.database else authority

    def as_log_kwargs(self) -> dict[str, Any]:
        """Structlog kwargs. Deliberately no credentials -- only host/port/name."""
        return {
            "db_host": self.host,
            "db_port": self.port,
            "db_name": self.database,
            "db_is_local": self.is_local,
        }


def _local_target(host: str, port: str | None, database: str | None) -> DatabaseTarget:
    return DatabaseTarget(host=host, port=port, database=database, is_local=True)


def describe_database_target(url: str | None) -> DatabaseTarget:
    """Parse a SQLAlchemy/Postgres DSN into a `DatabaseTarget`.

    Handles the shapes that actually appear in this repo and in the wild:

      postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev
      postgresql://postgres.<ref>:<pw>@aws-0-x.pooler.supabase.com:6543/postgres
      postgresql://user@host/db                       (no port)
      postgresql://[::1]:5432/db                      (IPv6 literal)
      postgresql:///nexus_dev                         (no authority: local socket)

    Never raises. An empty or unparseable DSN yields a local target whose host
    is an explicit placeholder rather than a bare empty string, so a caller that
    logs it shows the reader that parsing failed instead of showing nothing.
    """
    raw = (url or "").strip()
    if not raw:
        return _local_target("<unset>", None, None)

    try:
        parts = urlsplit(raw)
        host = (parts.hostname or "").strip().lower()
        # `parts.port` raises ValueError on a non-numeric port. That is not
        # cosmetic: for `postgresql://u:p@localhost:5432.evil.com/db` the
        # hostname still comes back as `localhost`, so swallowing the error and
        # keeping the host would report a malformed DSN as confidently local and
        # wave it past the migration gate. A port we cannot read means we cannot
        # prove where this points, so it is named and treated as remote.
        try:
            port: str | None = str(parts.port) if parts.port else None
        except ValueError:
            return DatabaseTarget(
                host="<invalid-port>",
                port=None,
                database=(parts.path or "").lstrip("/") or None,
                is_local=False,
            )
    except ValueError:
        return _local_target("<unparseable>", None, None)

    database = (parts.path or "").lstrip("/") or None

    if not host:
        # No authority component at all -- a Unix-socket DSN or a sqlite path.
        # Local by definition, and named so the log does not read as blank.
        return _local_target("<local-socket>", None, database)

    return DatabaseTarget(
        host=host,
        port=port,
        database=database,
        is_local=host in _LOCAL_HOSTS,
    )


def remote_migrations_allowed(env: Mapping[str, str] | None = None) -> bool:
    """Has the operator explicitly authorized migrating a remote database?

    Takes an optional mapping so the decision is testable without mutating
    `os.environ`; defaults to the real environment.
    """
    source: Mapping[str, str] = os.environ if env is None else env
    return source.get(ALLOW_REMOTE_MIGRATIONS_ENV, "").strip().lower() in _TRUTHY


def migration_target_refusal(target: DatabaseTarget, command: str) -> str:
    """The refusal text. Explains the fix rather than only stating the refusal."""
    return (
        f"REFUSING to run `alembic {command}` against a non-local database.\n"
        f"\n"
        f"  target:   {target.describe()}\n"
        f"  from:     backend/.env (DATABASE_URL)\n"
        f"\n"
        f"This changes a live schema. It is almost never what you want by\n"
        f"accident -- `backend/.env` is tracked by nobody and set by whoever ran\n"
        f"the app last, so it silently points wherever that person pointed it.\n"
        f"\n"
        f"If you meant the local database, start it and override the DSN:\n"
        f"    docker compose up -d postgres\n"
        f"    DATABASE_URL=postgresql+asyncpg://nexus:nexus@localhost:5432/nexus_dev"
        f" uv run alembic upgrade head\n"
        f"\n"
        f"If you really do mean this remote database, opt in explicitly:\n"
        f"    {ALLOW_REMOTE_MIGRATIONS_ENV}=1 uv run alembic upgrade head\n"
        f"\n"
        f"Read-only commands (`history`, `current`, `revision --autogenerate`)\n"
        f"are not gated and need no opt-in."
    )
