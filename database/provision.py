"""Owner-side database provisioning: migrations, LangGraph checkpoint tables and least-privilege grants.

Two database roles (docs/operations/DATABASE_ROLES.md):

  owner    owns the database and every object in it; the ONLY role that may run DDL. Used by the one-shot
           bootstrap step (this module) and by nothing that serves requests.
  runtime  what the API and the operator CLI connect as. Plain DML on application tables, read-only on
           `alembic_version`, no CREATE anywhere, owns nothing, not a superuser.

`bootstrap` is idempotent and additive: it never drops, truncates or rewrites data. Re-running it after a new
migration also covers the new tables, because the grants are applied to ALL existing tables as well as set as
default privileges for the owner's future ones.
"""

from __future__ import annotations

import re
from typing import Final

import psycopg
from alembic import command
from alembic.config import Config
from psycopg import sql
from sqlalchemy.engine import make_url

from core.memory.checkpoint import SCHEMA as CHECKPOINT_SCHEMA
from core.memory.checkpoint import checkpoint_dsn, provision_checkpoint_tables

APP_SCHEMAS: Final = ("public", CHECKPOINT_SCHEMA)
_ROLE = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class ProvisionError(Exception):
    pass


def _dsn(url: str) -> str:
    return checkpoint_dsn(url)


def _role(name: str) -> str:
    if not _ROLE.fullmatch(name):
        raise ProvisionError("runtime role must match [a-z_][a-z0-9_]* (max 63 characters)")
    return name


def apply_runtime_grants(owner_url: str, runtime_role: str) -> None:
    """Give `runtime_role` DML on the application schemas and nothing else. Idempotent. Run as the OWNER."""
    role = sql.Identifier(_role(runtime_role))
    with psycopg.connect(_dsn(owner_url), autocommit=True) as conn:
        owner = conn.execute("SELECT current_user").fetchone()
        if owner is None or owner[0] == runtime_role:
            raise ProvisionError("the owner and the runtime role must be different roles")
        db = sql.Identifier(
            conn.execute("SELECT current_database()").fetchone()[0]  # type: ignore[index]
        )
        # The runtime role may connect, and may NOT create schemas.
        conn.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(db, role))
        conn.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM {}").format(db, role))
        conn.execute(sql.SQL("REVOKE CREATE ON DATABASE {} FROM PUBLIC").format(db))
        conn.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        for schema in APP_SCHEMAS:
            s = sql.Identifier(schema)
            exists = conn.execute(
                "SELECT 1 FROM pg_namespace WHERE nspname = %s", (schema,)
            ).fetchone()
            if exists is None:
                continue
            conn.execute(sql.SQL("REVOKE CREATE ON SCHEMA {} FROM {}").format(s, role))
            conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(s, role))
            conn.execute(
                sql.SQL(
                    "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA {} TO {}"
                ).format(s, role)
            )
            conn.execute(
                sql.SQL("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA {} TO {}").format(s, role)
            )
            # Objects the owner creates LATER (the next migration) are covered without a re-grant.
            conn.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
                ).format(s, role)
            )
            conn.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA {} GRANT USAGE, SELECT ON SEQUENCES TO {}"
                ).format(s, role)
            )
        # The migration history is the owner's: the runtime role may read the revision, never change it.
        has_version = conn.execute("SELECT to_regclass('public.alembic_version')").fetchone()
        if has_version is not None and has_version[0] is not None:
            conn.execute(
                sql.SQL("REVOKE INSERT, UPDATE, DELETE ON public.alembic_version FROM {}").format(
                    role
                )
            )


def upgrade(owner_url: str) -> None:
    cfg = Config("alembic.ini")
    rendered = make_url(owner_url).render_as_string(hide_password=False).replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", rendered)
    command.upgrade(cfg, "head")


def bootstrap(owner_url: str, runtime_role: str) -> None:
    """migrations -> checkpoint tables -> grants, all as the owner. Safe to repeat."""
    _role(runtime_role)
    upgrade(owner_url)
    provision_checkpoint_tables(owner_url)
    apply_runtime_grants(owner_url, runtime_role)


def runtime_role_from_url(runtime_url: str) -> str:
    user = make_url(runtime_url).username
    if not user:
        raise ProvisionError("the runtime URL has no user name")
    return user
