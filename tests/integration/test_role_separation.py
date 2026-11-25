# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc"
"""Database role separation (P1.4), proven against an ISOLATED real PostgreSQL database. Synthetic data only.

The roles, the database and the schema objects are created fresh with random names by the repository's own init script
(`infrastructure/postgres/01-init.sh` in split-role mode) and dropped afterwards. No existing database is touched.

Invariants: the OWNER role runs DDL (migrations, checkpoint tables); the RUNTIME role can do ordinary DML and nothing
else - it cannot create, alter, drop or truncate anything, cannot change the migration history, owns nothing - and the
whole application (finance, memory, orchestrator with checkpoints) still works as that role.
"""

import os
import subprocess
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import httpx
import psycopg
import pytest
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from core.memory.checkpoint import open_checkpointer
from core.memory.crypto import Envelope
from core.memory.runtime import build_provider
from database import provision
from services.api.deployment_guard import (
    DeploymentConfigError,
    assert_least_privilege,
    privilege_problems,
)
from services.api.main import create_app
from tests.integration.conftest import ADMIN_URL, Api
from tests.integration.helpers import enroll_device, make_settings, make_user
from tests.integration.test_finance import bank, get, post, txn
from tests.integration.test_memory_checkpoints import lg_rows
from tests.integration.test_memory_checkpoints import run as run_graph
from tests.integration.test_orchestrator import run as orchestrate
from tests.integration.test_orchestrator import seed_registry_and_bootstrap

REPO = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(ADMIN_URL is None, reason="ABU_TEST_ADMIN_URL not set")


@dataclass
class Split:
    db: str
    owner: str
    runtime: str
    owner_url: str
    runtime_url: str
    admin_db_url: str
    env: dict[str, str]


def _dsn(url: str) -> str:
    return url.replace("postgresql+psycopg://", "postgresql://", 1)


@pytest.fixture(scope="module")
def split() -> Iterator[Split]:
    assert ADMIN_URL is not None
    admin = make_url(ADMIN_URL)
    tag = uuid.uuid4().hex[:10]
    names = {
        "db": f"abu_split_{tag}",
        "owner": f"abu_own_{tag}",
        "runtime": f"abu_rt_{tag}",
        "ll_db": f"abu_ll_{tag}",
        "ll_user": f"abu_llu_{tag}",
    }
    own_pw, rt_pw, ll_pw = (uuid.uuid4().hex for _ in range(3))
    env = {
        **os.environ,
        "PGHOST": admin.host or "127.0.0.1",
        "PGPORT": str(admin.port or 5432),
        "PGPASSWORD": admin.password or "",
        "POSTGRES_USER": admin.username or "",
        "APP_DB_NAME": names["db"],
        "APP_DB_USER": names["runtime"],
        "APP_DB_PASSWORD": rt_pw,
        "APP_DB_OWNER_USER": names["owner"],
        "APP_DB_OWNER_PASSWORD": own_pw,
        "LITELLM_DB_NAME": names["ll_db"],
        "LITELLM_DB_USER": names["ll_user"],
        "LITELLM_DB_PASSWORD": ll_pw,
    }
    subprocess.run(
        ["bash", str(REPO / "infrastructure/postgres/01-init.sh")], env=env, check=True, cwd=REPO
    )
    host, port = admin.host, admin.port or 5432
    owner_url = f"postgresql+psycopg://{names['owner']}:{own_pw}@{host}:{port}/{names['db']}"
    runtime_url = f"postgresql+psycopg://{names['runtime']}:{rt_pw}@{host}:{port}/{names['db']}"
    admin_db_url = admin.set(database=names["db"]).render_as_string(hide_password=False)
    provision.bootstrap(owner_url, names["runtime"])
    try:
        yield Split(
            names["db"], names["owner"], names["runtime"], owner_url, runtime_url, admin_db_url, env
        )
    finally:
        with psycopg.connect(_dsn(ADMIN_URL), autocommit=True) as c:
            for d in (names["db"], names["ll_db"]):
                c.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
            for r in (names["owner"], names["runtime"], names["ll_user"]):
                c.execute(f'DROP ROLE IF EXISTS "{r}"')


def _denied(url: str, statement: str) -> str:
    """Run one statement as that role; it must be refused for lack of privilege. Returns the SQLSTATE."""
    with psycopg.connect(_dsn(url), autocommit=True) as c:
        with pytest.raises(psycopg.Error) as err:
            c.execute(statement)
        return str(err.value.sqlstate)


# --------------------------------------------------------------------------------------------------- the roles
def test_the_split_has_two_distinct_roles_and_the_owner_owns_the_database(split: Split) -> None:
    with psycopg.connect(_dsn(split.admin_db_url), autocommit=True) as c:
        rows: dict[str, bool] = dict(
            c.execute(
                "SELECT rolname, rolsuper OR rolcreaterole OR rolcreatedb FROM pg_roles WHERE rolname = ANY(%s)",
                ([split.owner, split.runtime],),
            ).fetchall()
        )
        assert rows == {split.owner: False, split.runtime: False}
        db_owner = c.execute(
            "SELECT pg_get_userbyid(datdba) FROM pg_database WHERE datname = %s", (split.db,)
        ).fetchone()[0]
        assert db_owner == split.owner
        tables_owned_by_runtime = c.execute(
            "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname IN ('public','lg_checkpoints') AND pg_get_userbyid(c.relowner) = %s",
            (split.runtime,),
        ).fetchone()[0]
        assert tables_owned_by_runtime == 0


def test_migrations_and_checkpoint_tables_were_created_by_the_owner(split: Split) -> None:
    with psycopg.connect(_dsn(split.owner_url)) as c:
        head = c.execute("SELECT version_num FROM alembic_version").fetchone()[0]
        assert head == "0012"
        assert (
            c.execute("SELECT to_regclass('lg_checkpoints.checkpoints')").fetchone()[0] is not None
        )
        assert c.execute("SELECT to_regclass('public.fin_transactions')").fetchone()[0] is not None


@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE public.rogue (id int)",
        "CREATE TABLE lg_checkpoints.rogue (id int)",
        "CREATE SCHEMA rogue",
        "CREATE EXTENSION IF NOT EXISTS pg_trgm",
        "ALTER TABLE public.users ADD COLUMN rogue int",
        "ALTER TABLE public.users DISABLE TRIGGER ALL",
        "DROP TABLE public.fin_transactions",
        "DROP TABLE lg_checkpoints.checkpoints",
        "TRUNCATE public.audit_events",
        "TRUNCATE lg_checkpoints.checkpoints",
        "DROP INDEX public.uq_fin_txn_transfer_peer",
        "CREATE INDEX rogue_idx ON public.users (display_name)",
        "DROP FUNCTION public.fin_txn_peer_integrity()",
        "UPDATE public.alembic_version SET version_num = '0001'",
        "DELETE FROM public.alembic_version",
        "INSERT INTO public.alembic_version VALUES ('9999')",
        "CREATE ROLE rogue",
        "ALTER ROLE CURRENT_USER SUPERUSER",
        "ALTER TABLE public.users OWNER TO CURRENT_USER",
        "COMMENT ON TABLE public.users IS 'x'",
    ],
)
def test_the_runtime_role_cannot_change_the_schema_or_the_migration_history(
    split: Split, statement: str
) -> None:
    assert _denied(split.runtime_url, statement) in {"42501", "42601"}, statement
    # and nothing leaked through: the object set is exactly what the owner created
    with psycopg.connect(_dsn(split.owner_url)) as c:
        assert c.execute("SELECT to_regclass('public.rogue')").fetchone()[0] is None
        assert c.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 1


def test_a_runtime_role_grant_changes_nothing(split: Split) -> None:
    """A non-owner GRANT is not an error in PostgreSQL (it warns) but it must not grant anything."""
    with psycopg.connect(_dsn(split.runtime_url), autocommit=True) as c:
        c.execute("GRANT ALL ON public.users TO PUBLIC")
    with psycopg.connect(_dsn(split.owner_url)) as c:
        assert not c.execute(
            "SELECT has_table_privilege('public', 'public.users', 'SELECT')"
        ).fetchone()[0]


def test_the_runtime_role_has_exactly_the_privileges_the_application_needs(split: Split) -> None:
    with psycopg.connect(_dsn(split.runtime_url), autocommit=True) as c:
        assert c.execute("SELECT version_num FROM alembic_version").fetchone()[0] == "0012"
        # ordinary DML on a checkpoint table and on an application table
        c.execute(
            "INSERT INTO lg_checkpoints.checkpoint_writes "
            "(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel, type, blob) "
            "VALUES ('t','', 'c', 'k', 0, 'ch', 'x', '\\x00')"
        )
        c.execute("DELETE FROM lg_checkpoints.checkpoint_writes WHERE thread_id = 't'")
        assert c.execute("SELECT count(*) FROM public.users").fetchone()[0] == 0
        # sequences are usable (identity/serial columns)
        assert c.execute(
            "SELECT has_sequence_privilege(current_user, c.oid, 'USAGE') FROM pg_class c "
            "WHERE c.relkind = 'S' AND c.relnamespace = 'public'::regnamespace LIMIT 1"
        ).fetchone() in (None, (True,))


def test_tables_created_later_by_the_owner_are_covered_and_bootstrap_is_idempotent(
    split: Split,
) -> None:
    with psycopg.connect(_dsn(split.owner_url), autocommit=True) as c:
        c.execute("CREATE TABLE public.later_table (id int PRIMARY KEY, v text)")
    with psycopg.connect(_dsn(split.runtime_url), autocommit=True) as c:
        c.execute(
            "INSERT INTO public.later_table VALUES (1, 'x')"
        )  # default privileges: no re-grant needed
        c.execute("UPDATE public.later_table SET v = 'y'")
        c.execute("DELETE FROM public.later_table")
    provision.apply_runtime_grants(
        split.owner_url, split.runtime
    )  # run it again: no error, same result
    provision.apply_runtime_grants(split.owner_url, split.runtime)
    with psycopg.connect(_dsn(split.owner_url), autocommit=True) as c:
        c.execute("DROP TABLE public.later_table")


def test_bootstrap_refuses_bad_input(split: Split) -> None:
    with pytest.raises(provision.ProvisionError):
        provision.apply_runtime_grants(split.owner_url, split.owner)  # owner == runtime
    with pytest.raises(provision.ProvisionError):
        provision.bootstrap(split.owner_url, 'x"; DROP TABLE users; --')  # not an identifier


# ------------------------------------------------------------------------------------------- the privilege guard
async def test_the_privilege_guard_accepts_the_runtime_role_and_rejects_the_others(
    split: Split,
) -> None:
    runtime = create_async_engine(split.runtime_url)
    owner = create_async_engine(split.owner_url)
    admin = create_async_engine(split.admin_db_url)
    try:
        assert await privilege_problems(runtime) == []
        await assert_least_privilege(runtime)
        owner_problems = await privilege_problems(owner)
        assert any("CREATE in schema public" in p for p in owner_problems)
        assert any("owns" in p for p in owner_problems)
        assert any("SUPERUSER" in p for p in await privilege_problems(admin))
        with pytest.raises(DeploymentConfigError):
            await assert_least_privilege(owner)
    finally:
        for e in (runtime, owner, admin):
            await e.dispose()


# ---------------------------------------------------------------- the whole application, as the runtime role
@pytest.fixture
async def runtime_api(split: Split) -> Api:
    settings = make_settings(
        make_url(split.runtime_url),
        checkpoint_auto_setup=False,  # the runtime role has no DDL rights; the bootstrap step made the tables
    )
    engine = create_async_engine(split.runtime_url)
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    app = create_app(settings, startup_check=assert_least_privilege)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield Api(app, client, settings, sessions)
    await engine.dispose()


async def test_finance_memory_and_the_orchestrator_work_with_no_ddl_rights(
    runtime_api: Api,
) -> None:
    api = runtime_api
    user = await make_user(api.sessions, "Role test")
    dev = await enroll_device(api.sessions, api.settings, api.client, user)
    # finance: accounts, transactions, the S2.1A transfer trigger path
    a, b = await bank(api, dev), await bank(api, dev, last4="9999")
    await post(api, dev, "/transactions", txn(a, 5, "100.00", "debit", "NEFT TO SELF"), 201)
    await post(
        api, dev, "/transactions", txn(b, 6, "100.00", "credit", "SELF TRANSFER FROM X"), 201
    )
    rows = await get(api, dev, "/transactions", limit=50)
    assert len(rows) == 2 and all(r["transfer_peer_id"] for r in rows)
    # memory (encrypted records, embeddings, access log)
    r = await api.client.post(
        "/v1/memory",
        json={"namespace": "user/profile", "memory_type": "semantic", "text": "Role split works"},
        headers=dev.headers,
    )
    assert r.status_code == 201, r.text
    # orchestrator: registry/governance/executions/audit writes plus encrypted checkpoints, all as the runtime role
    await seed_registry_and_bootstrap(api)
    out = await orchestrate(api, dev, "Plan my day around my goals")
    assert out.status_code == 200, out.text
    assert out.json()["status"] == "completed"
    # the real encrypted LangGraph saver writes checkpoints as the runtime role (tables pre-created by the owner)
    thread = uuid.uuid4().hex
    await run_graph(api.app.state.memory.saver, thread)
    async with api.sessions() as s:
        rows = await lg_rows(s, thread)
    assert rows["checkpoints"] >= 1 and rows["checkpoint_blobs"] >= 1


async def test_the_runtime_checkpointer_needs_setup_disabled(
    split: Split, runtime_api: Api
) -> None:
    envelope = Envelope(build_provider(runtime_api.settings))
    # setup=True would run DDL as the runtime role and is correctly refused ...
    with pytest.raises(Exception) as err:  # noqa: PT011 - psycopg wraps the privilege error
        await open_checkpointer(split.runtime_url, envelope, setup=True)
    assert (
        "permission denied" in str(err.value).lower() or "must be owner" in str(err.value).lower()
    )
    # ... while setup=False (what staging/production configure) opens and reads fine
    pool, saver = await open_checkpointer(split.runtime_url, envelope, setup=False)
    try:
        assert (
            await saver.aget_tuple({"configurable": {"thread_id": "none", "checkpoint_ns": ""}})
            is None
        )
    finally:
        await pool.close()


# ------------------------------------------------------------------------------ the admin CLI wrapper, end to end
def test_the_admin_cli_bootstrap_command_works_with_only_the_owner_url(split: Split) -> None:
    env = {
        **os.environ,
        "ABU_MIGRATION_DATABASE_URL": split.owner_url,
        "ABU_RUNTIME_DB_ROLE": split.runtime,
    }
    for var in ("ABU_DATABASE_URL", "ABU_JWT_SECRET", "ABU_TOKEN_HASH_KEY"):
        env.pop(var, None)  # the owner-side step needs none of the runtime secrets
    done = subprocess.run(
        [
            ".venv/bin/python" if (REPO / ".venv/bin/python").exists() else "python",
            "-m",
            "services.admin",
            "db",
            "bootstrap",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode == 0, done.stderr
    assert "bootstrap complete" in done.stdout
    assert split.owner_url.split(":")[2].split("@")[0] not in done.stdout  # no password echoed
    assert split.owner_url.split(":")[2].split("@")[0] not in done.stderr


def test_the_admin_cli_bootstrap_command_requires_the_owner_url() -> None:
    env = {k: v for k, v in os.environ.items() if k != "ABU_MIGRATION_DATABASE_URL"}
    done = subprocess.run(
        [
            ".venv/bin/python" if (REPO / ".venv/bin/python").exists() else "python",
            "-m",
            "services.admin",
            "db",
            "bootstrap",
            "--runtime-role",
            "x",
        ],
        cwd=REPO,
        env=env,
        capture_output=True,
        text=True,
    )
    assert done.returncode != 0 and "ABU_MIGRATION_DATABASE_URL" in (done.stderr + done.stdout)


# ------------------------------------------------ rehearsal of the OPTIONAL split of an existing single-role database
def test_an_existing_single_role_database_can_be_split_without_losing_a_row() -> None:
    """Throwaway database only. Proves the operator procedure (infrastructure/postgres/split-existing-db.sql) changes
    ownership and nothing else. It is NOT run against any real database."""
    assert ADMIN_URL is not None
    admin = make_url(ADMIN_URL)
    tag = uuid.uuid4().hex[:10]
    db, runtime, owner, ll_db, ll_user = (
        f"abu_old_{tag}",
        f"abu_oldrt_{tag}",
        f"abu_oldown_{tag}",
        f"abu_oldll_{tag}",
        f"abu_oldllu_{tag}",
    )
    rt_pw, own_pw, ll_pw = (uuid.uuid4().hex for _ in range(3))
    env = {
        **os.environ,
        "PGHOST": admin.host or "127.0.0.1",
        "PGPORT": str(admin.port or 5432),
        "PGPASSWORD": admin.password or "",
        "POSTGRES_USER": admin.username or "",
        "APP_DB_NAME": db,
        "APP_DB_USER": runtime,
        "APP_DB_PASSWORD": rt_pw,
        "LITELLM_DB_NAME": ll_db,
        "LITELLM_DB_USER": ll_user,
        "LITELLM_DB_PASSWORD": ll_pw,
    }
    env.pop("APP_DB_OWNER_USER", None)  # legacy single-role initialisation
    subprocess.run(
        ["bash", str(REPO / "infrastructure/postgres/01-init.sh")], env=env, check=True, cwd=REPO
    )
    host, port = admin.host, admin.port or 5432
    legacy_url = f"postgresql+psycopg://{runtime}:{rt_pw}@{host}:{port}/{db}"
    owner_url = f"postgresql+psycopg://{owner}:{own_pw}@{host}:{port}/{db}"
    try:
        provision.upgrade(legacy_url)  # the old way: the runtime role migrates and owns everything
        with psycopg.connect(_dsn(legacy_url), autocommit=True) as c:
            c.execute("INSERT INTO users (display_name) VALUES ('before-split'), ('also-before')")
            assert c.execute("SELECT count(*) FROM users").fetchone()[0] == 2
        subprocess.run(
            [
                "psql",
                "-v",
                "ON_ERROR_STOP=1",
                "-U",
                admin.username or "",
                "-d",
                "postgres",
                "-v",
                f"db={db}",
                "-v",
                f"runtime={runtime}",
                "-v",
                f"owner={owner}",
                "-v",
                f"owner_pw={own_pw}",
                "-f",
                str(REPO / "infrastructure/postgres/split-existing-db.sql"),
            ],
            env=env,
            check=True,
            capture_output=True,
            text=True,
        )
        provision.bootstrap(
            owner_url, runtime
        )  # already at head; creates checkpoint tables and applies grants
        with psycopg.connect(_dsn(owner_url)) as c:
            assert c.execute("SELECT count(*) FROM users").fetchone()[0] == 2  # data untouched
            assert c.execute("SELECT count(*) FROM alembic_version").fetchone()[0] == 1
            owned_by_runtime = c.execute(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                "WHERE n.nspname IN ('public','lg_checkpoints') AND pg_get_userbyid(c.relowner) = %s",
                (runtime,),
            ).fetchone()[0]
            assert owned_by_runtime == 0
        assert _denied(legacy_url, "CREATE TABLE public.rogue (id int)") == "42501"
        assert _denied(legacy_url, "DROP TABLE public.users") == "42501"
        with psycopg.connect(_dsn(legacy_url), autocommit=True) as c:
            assert (
                c.execute("SELECT count(*) FROM users").fetchone()[0] == 2
            )  # runtime DML still works
            c.execute("DELETE FROM users WHERE display_name = 'also-before'")
    finally:
        with psycopg.connect(_dsn(ADMIN_URL), autocommit=True) as c:
            for d in (db, ll_db):
                c.execute(f'DROP DATABASE IF EXISTS "{d}" WITH (FORCE)')
            for r in (owner, runtime, ll_user):
                c.execute(f'DROP ROLE IF EXISTS "{r}"')
