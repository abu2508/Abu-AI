"""Fail-closed deployment guard for staging and production.

`create_app` is deliberately permissive (development, tests and the OpenAPI export all build it with partial settings).
The container entrypoint (`services.api.entrypoint:create_checked_app`) runs these checks first, so a deployment with a
missing or unsafe setting refuses to start instead of running degraded. In development and test the guard does nothing:
the existing defaults are unchanged.

Messages name the setting and the rule, never a value.
"""

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from core.memory.embeddings import REGISTRY
from services.api.config import Settings

GUARDED_ENVS = ("staging", "production")


class DeploymentConfigError(RuntimeError):
    pass


def deployment_problems(s: Settings) -> list[str]:
    if s.env not in GUARDED_ENVS:
        return []
    p: list[str] = []
    if s.database_url is None:
        p.append("ABU_DATABASE_URL is not set")
    if s.jwt_secret is None or s.token_hash_key is None:
        p.append("ABU_JWT_SECRET and ABU_TOKEN_HASH_KEY must both be set")
    elif s.jwt_secret.get_secret_value() == s.token_hash_key.get_secret_value():
        p.append("ABU_JWT_SECRET and ABU_TOKEN_HASH_KEY must differ")
    if s.litellm_master_key is None:
        p.append("ABU_LITELLM_MASTER_KEY is not set")
    if not s.memory_keyring_file:
        p.append("ABU_MEMORY_KEYRING_FILE is not set (a keyring FILE is required)")
    if s.memory_kek is not None:
        p.append("ABU_MEMORY_KEK must not be set: key material comes from the keyring file only")
    if s.trusted_proxy_hops != 1:
        p.append("ABU_TRUSTED_PROXY_HOPS must be 1 (exactly one trusted reverse proxy, Caddy)")
    if s.migration_database_url is not None:
        p.append(
            "ABU_MIGRATION_DATABASE_URL (the OWNER role) must not be present in the runtime environment"
        )
    if s.checkpoint_auto_setup:
        p.append("ABU_CHECKPOINT_AUTO_SETUP must be false: the runtime role has no DDL rights")
    spec = REGISTRY.get(s.embedder)
    if spec is None:
        p.append("ABU_EMBEDDER names an unknown embedder")
    elif not spec.factory().is_semantic:
        if s.env == "production":
            p.append(
                "ABU_EMBEDDER is a TEST embedder: refused in production (gate G4: no approved production embedder)"
            )
        elif not s.allow_test_embedder_in_production:
            p.append(
                "ABU_EMBEDDER is a TEST embedder: staging needs ABU_ALLOW_TEST_EMBEDDER_IN_PRODUCTION=true, "
                "an explicit statement that only synthetic data is used"
            )
    return p


def enforce(s: Settings) -> None:
    problems = deployment_problems(s)
    if problems:
        raise DeploymentConfigError(f"refusing to start ABU_ENV={s.env}: " + "; ".join(problems))


async def privilege_problems(engine: AsyncEngine) -> list[str]:
    """What the connected role can do that a runtime role must not. Read-only catalogue queries."""
    p: list[str] = []
    async with engine.connect() as c:
        role = (
            await c.execute(
                text(
                    "SELECT rolsuper, rolcreaterole, rolcreatedb, rolbypassrls, rolreplication "
                    "FROM pg_roles WHERE rolname = current_user"
                )
            )
        ).one()
        for flag, name in zip(
            role, ("SUPERUSER", "CREATEROLE", "CREATEDB", "BYPASSRLS", "REPLICATION"), strict=True
        ):
            if flag:
                p.append(f"the runtime database role has {name}")
        for schema in ("public", "lg_checkpoints"):
            can = (
                await c.execute(
                    text(
                        "SELECT CASE WHEN to_regnamespace(:s) IS NULL THEN false "
                        "ELSE has_schema_privilege(current_user, :s, 'CREATE') END"
                    ),
                    {"s": schema},
                )
            ).scalar_one()
            if can:
                p.append(f"the runtime database role can CREATE in schema {schema}")
        if (
            await c.execute(
                text("SELECT has_database_privilege(current_user, current_database(), 'CREATE')")
            )
        ).scalar_one():
            p.append("the runtime database role can CREATE schemas in the database")
        owned = (
            await c.execute(
                text(
                    "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
                    "WHERE c.relowner = (SELECT oid FROM pg_roles WHERE rolname = current_user) "
                    "AND n.nspname IN ('public', 'lg_checkpoints') AND c.relkind IN ('r','p','v','m','S')"
                )
            )
        ).scalar_one()
        if owned:
            p.append(
                f"the runtime database role owns {owned} application object(s) (it must own none)"
            )
    return p


async def assert_least_privilege(engine: AsyncEngine) -> None:
    problems = await privilege_problems(engine)
    if problems:
        raise DeploymentConfigError("refusing to start: " + "; ".join(problems))
