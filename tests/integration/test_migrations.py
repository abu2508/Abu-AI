from collections.abc import Callable

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError

from database.schemas import Base
from tests.integration.conftest import alembic_config

REGISTRY = {
    "model_provider_policies",
    "models",
    "model_capabilities",
    "model_pricing",
    "model_routes",
}
AGENTS_0002 = {"agents", "agent_model_permissions"}
AUTH = {"users", "auth_sessions", "refresh_tokens", "enrollment_codes", "audit_events"}
AGENTS_HEAD = {
    "agents",
    "agent_versions",
    "agent_version_model_aliases",
    "agent_version_tools",
    "agent_version_memory",
    "tool_approvals",
}
GOVERNANCE = {
    "kill_switch_state",
    "spending_config",
    "executions",
    "execution_calls",
    "spend_ledger",
}
MEMORY = {"memory_records", "memory_embeddings", "memory_access_logs", "agent_threads"}
HEAD = REGISTRY | AUTH | AGENTS_HEAD | GOVERNANCE | MEMORY


def tables(url: URL) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def test_fresh_database_upgrade(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    assert tables(url) == HEAD


def test_stepwise_upgrade_preserves_data_and_downgrades(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0001")
    assert tables(url) == REGISTRY
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO model_provider_policies (provider, retention_summary, trains_on_data, "
                "human_review, max_data_class) VALUES ('p','r','no','no','synthetic')"
            )
        )
    command.upgrade(cfg, "0002")
    assert tables(url) == REGISTRY | AGENTS_0002
    # Legacy (Milestone 2) agent-level permissions, which 0004 must carry over without widening.
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO agents (agent_id, name, description, enabled) VALUES ('legacy_agent','L','d',true)"
            )
        )
        c.execute(
            text("INSERT INTO agents (agent_id, name, description) VALUES ('idle_agent','I','d')")
        )
        c.execute(text("INSERT INTO agent_model_permissions VALUES ('legacy_agent','test-mock')"))

    command.upgrade(cfg, "0003")
    assert tables(url) == REGISTRY | AGENTS_0002 | AUTH
    command.upgrade(cfg, "0004")
    assert tables(url) == REGISTRY | AUTH | AGENTS_HEAD
    command.upgrade(cfg, "0005")  # governance
    assert tables(url) == REGISTRY | AUTH | AGENTS_HEAD | GOVERNANCE
    command.upgrade(cfg, "head")  # 0006: memory
    assert tables(url) == HEAD
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM model_provider_policies")).scalar_one() == 1
        rows = c.execute(
            text(
                "SELECT v.agent_id, v.version, v.state, a.alias FROM agent_versions v "
                "LEFT JOIN agent_version_model_aliases a ON a.version_id = v.id ORDER BY 1"
            )
        ).all()
        assert rows == [
            ("legacy_agent", 1, "active", "test-mock")
        ]  # idle agent: no permissions, no version yet
        assert (
            c.execute(text("SELECT enabled FROM agents WHERE agent_id='legacy_agent'")).scalar_one()
            is True
        )

    command.downgrade(
        cfg, "0005"
    )  # drops the memory tables (no checkpoint data exists, so RESTRICT is satisfied)
    assert tables(url) == REGISTRY | AUTH | AGENTS_HEAD | GOVERNANCE
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0004")  # drops governance tables and the refresh-retry columns
    assert tables(url) == REGISTRY | AUTH | AGENTS_HEAD
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0003")  # restores the legacy permission table from the active version
    with eng.connect() as c:
        assert c.execute(
            text("SELECT agent_id, route_alias FROM agent_model_permissions")
        ).all() == [("legacy_agent", "test-mock")]
    command.downgrade(cfg, "base")
    assert tables(url) == set()
    command.upgrade(cfg, "head")  # and back up again
    assert tables(url) == HEAD
    eng.dispose()


def test_audit_trigger_removed_cleanly_on_downgrade(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "0002")
    eng = create_engine(url)
    with eng.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname='audit_events_append_only'")
            ).scalar_one()
            == 0
        )
    eng.dispose()


def test_upgrade_is_idempotent(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    command.upgrade(cfg, "head")  # no-op, no error


def test_orm_models_match_migrations(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    eng = create_engine(url)
    with eng.connect() as c:
        diff = compare_metadata(
            MigrationContext.configure(c, opts={"compare_type": True}), Base.metadata
        )
    eng.dispose()
    assert diff == []


def test_database_refuses_enabled_unapproved_model(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    eng = create_engine(url)
    with eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO model_provider_policies (id, provider, retention_summary, trains_on_data, "
                "human_review, max_data_class) VALUES (gen_random_uuid(),'p','r','no','no','synthetic')"
            )
        )
    with pytest.raises(IntegrityError), eng.begin() as c:
        c.execute(
            text(
                "INSERT INTO models (name, policy_id, provider_model_id, gateway_model_name, enabled) "
                "SELECT 'm', id, 'x', 'x', true FROM model_provider_policies"
            )
        )
    eng.dispose()


def test_governance_singletons_are_seeded_safe_by_default(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    eng = create_engine(url)
    with eng.connect() as c:
        assert c.execute(text("SELECT active FROM kill_switch_state")).all() == [(False,)]
        row = c.execute(
            text(
                "SELECT monthly_budget_inr, daily_budget_inr, paid_models_enabled, paid_frozen, usd_inr_rate "
                "FROM spending_config"
            )
        ).one()
        assert (row[0], row[1], row[2], row[3], row[4]) == (
            0,
            0,
            False,
            False,
            None,
        )  # default spend: INR 0
    eng.dispose()


def test_memory_migration_sets_up_pgvector_checkpoint_schema_and_guards(
    scratch_db: Callable[[], URL],
) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "head")
    eng = create_engine(url)
    with eng.connect() as c:
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_extension WHERE extname = 'vector'")
            ).scalar_one()
            == 1
        )
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'lg_checkpoints'"
                )
            ).scalar_one()
            == 1
        )
        # nothing from LangGraph (or anything else) is created by the migration inside that schema:
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'lg_checkpoints'"
                )
            ).scalar_one()
            == 0
        )
        dim = c.execute(
            text(
                "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class t ON t.oid = a.attrelid WHERE t.relname = 'memory_embeddings' AND a.attname = 'embedding'"
            )
        ).scalar_one()
        assert dim == 256  # the dimension is fixed by the migration; changing it needs a new one
        index = c.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname = 'ix_memory_embeddings_hnsw'")
        ).scalar_one()
        assert "hnsw" in index and "vector_cosine_ops" in index
        checks = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT conname FROM pg_constraint WHERE conrelid = 'memory_records'::regclass AND contype = 'c'"
                )
            )
        }
        assert {
            "ck_memory_records_no_third_party_procedural",
            "ck_memory_records_structured_not_embedded",
            "ck_memory_records_third_party_embedding_needs_confirmation",
        } <= checks

    # LangGraph's own tables hold working memory. The downgrade must REFUSE rather than destroy them.
    with eng.begin() as c:
        c.execute(text("CREATE TABLE lg_checkpoints.checkpoints (thread_id text)"))
    with pytest.raises(Exception, match="lg_checkpoints|depend"):
        command.downgrade(cfg, "0005")
    with eng.begin() as c:
        c.execute(text("DROP TABLE lg_checkpoints.checkpoints"))
    command.downgrade(cfg, "0005")  # after the operator has deleted the threads it succeeds
    with eng.connect() as c:
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM information_schema.schemata WHERE schema_name = 'lg_checkpoints'"
                )
            ).scalar_one()
            == 0
        )
    eng.dispose()
