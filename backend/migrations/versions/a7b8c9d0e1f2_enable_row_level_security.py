"""enable row level security on every table in the public schema

Revision ID: a7b8c9d0e1f2
Revises: f6a7b8c9d0e1
Create Date: 2026-09-30 12:00:00.000000

Closes Supabase Shield advisories ``rls_disabled_in_public`` and
``sensitive_columns_exposed`` on hosted Supabase projects.

Every table in the ``public`` schema shipped with RLS disabled and no policies.
That is only safe on a database whose sole client is the backend, and this one
is hosted: PostgREST also serves ``public`` at ``/rest/v1/<table>``, and the
``anon`` key is *designed to be published* (it normally ships in browser
bundles). With RLS off, that publishable key is a full read/write/delete key
on the whole schema - including ``users.email``, ``users.hashed_password``,
``user_settings.totp_secret`` and ``api_keys.encrypted_key``.

The backend itself is unaffected. It connects with ``DATABASE_URL`` as the
table *owner* (``postgres``), and a table owner bypasses its own RLS unless
``FORCE ROW LEVEL SECURITY`` is set - which this migration deliberately does
not do. Storage calls authenticate with the ``service_role`` key, which also
carries ``BYPASSRLS``. Neither path is a ``public``-schema REST client, so
denying ``anon``/``authenticated`` removes no capability the app has.

Two parts:

1. Enable RLS on the 40 tables that exist now, and revoke the blanket grants
   Supabase gives ``anon``/``authenticated`` by default. With RLS enabled and
   no policies the default is deny, so the revoke is belt-and-braces against a
   future policy accidentally re-opening a table.
2. Install a ``ddl_command_end`` event trigger so tables created *later* are
   protected too. This matters because ``infrastructure/database/engine.py``
   calls ``create_all`` on startup: without the trigger, the next new SQLModel
   would silently land unprotected and the advisory would come back.

No policies are created, and no data is read, rewritten, or dropped. Adding
per-user policies later is a deliberate follow-up, not something to guess at:
the app authorizes every request in application code (``api/deps.py``), and
mirroring that in SQL policies is a larger design question than this migration.

Portability: the ``anon``/``authenticated`` roles only exist on hosted Supabase,
and the test suite runs against a plain Postgres container, so the revokes are
guarded by a role-existence check. ``ddl_command_end`` exists in every
supported PostgreSQL version.
"""
from typing import Sequence, Union

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: Union[str, None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# Loop over the live catalog rather than a hard-coded list: this keeps the
# migration correct whether the schema came from `create_all` (which builds
# every table the app imports) or from the hand-written DDL in this directory,
# and it cannot drift the way a fixed list would.
_ENABLE_RLS_LOOP = """
DO $$
DECLARE
    tbl record;
BEGIN
    FOR tbl IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
    LOOP
        EXECUTE format('ALTER TABLE public.%I ENABLE ROW LEVEL SECURITY', tbl.relname);
    END LOOP;
END
$$;
"""

# Keep new tables protected without anyone having to remember. Scoped to
# ddl_command_end + the CREATE TABLE tag so it cannot re-enter on the ALTER
# TABLE it issues (and the filter on schema_name keeps it out of pg_catalog).
_RLS_EVENT_TRIGGER = """
CREATE OR REPLACE FUNCTION public.nexus_enable_rls_on_new_table()
RETURNS event_trigger
LANGUAGE plpgsql
AS $$
DECLARE
    cmd record;
BEGIN
    FOR cmd IN SELECT * FROM pg_event_trigger_ddl_commands() LOOP
        IF cmd.command_tag = 'CREATE TABLE' AND cmd.schema_name = 'public' THEN
            EXECUTE format('ALTER TABLE %s ENABLE ROW LEVEL SECURITY', cmd.objid::regclass);
        END IF;
    END LOOP;
END
$$;

DROP EVENT TRIGGER IF EXISTS nexus_enable_rls_on_create;

CREATE EVENT TRIGGER nexus_enable_rls_on_create
    ON ddl_command_end
    WHEN TAG IN ('CREATE TABLE')
    EXECUTE FUNCTION public.nexus_enable_rls_on_new_table();
"""

# Supabase grants these roles ALL on public tables by default. Enabled RLS with
# no policies already denies them; revoking makes that independent of the RLS
# flag, so a future `ALTER TABLE ... DISABLE ROW LEVEL SECURITY` cannot silently
# re-open the schema. Guarded because neither role exists on plain Postgres.
_REVOKE_PUBLIC_GRANTS = """
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format('REVOKE ALL ON ALL TABLES IN SCHEMA public FROM %I', role_name);
            EXECUTE format('REVOKE ALL ON ALL SEQUENCES IN SCHEMA public FROM %I', role_name);
        END IF;
    END LOOP;
END
$$;
"""

_RESTORE_PUBLIC_GRANTS = """
DO $$
DECLARE
    role_name text;
BEGIN
    FOREACH role_name IN ARRAY ARRAY['anon', 'authenticated'] LOOP
        IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = role_name) THEN
            EXECUTE format(
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO %I',
                role_name
            );
            EXECUTE format('GRANT ALL ON ALL SEQUENCES IN SCHEMA public TO %I', role_name);
        END IF;
    END LOOP;
END
$$;
"""

_DISABLE_RLS_LOOP = """
DO $$
DECLARE
    tbl record;
BEGIN
    FOR tbl IN
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = 'public'
          AND c.relkind = 'r'
    LOOP
        EXECUTE format('ALTER TABLE public.%I DISABLE ROW LEVEL SECURITY', tbl.relname);
    END LOOP;
END
$$;
"""


def upgrade() -> None:
    op.execute(_ENABLE_RLS_LOOP)
    op.execute(_REVOKE_PUBLIC_GRANTS)
    op.execute(_RLS_EVENT_TRIGGER)


def downgrade() -> None:
    # Removes the automatic protection first, so nothing created during the
    # downgrade is left half-protected.
    op.execute("DROP EVENT TRIGGER IF EXISTS nexus_enable_rls_on_create")
    op.execute("DROP FUNCTION IF EXISTS public.nexus_enable_rls_on_new_table()")
    op.execute(_DISABLE_RLS_LOOP)
    op.execute(_RESTORE_PUBLIC_GRANTS)
