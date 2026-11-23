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
AGENTS = {"agents", "agent_model_permissions"}


def tables(url: URL) -> set[str]:
    eng = create_engine(url)
    try:
        return set(inspect(eng).get_table_names()) - {"alembic_version"}
    finally:
        eng.dispose()


def test_fresh_database_upgrade(scratch_db: Callable[[], URL]) -> None:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    assert tables(url) == REGISTRY | AGENTS


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
    command.upgrade(cfg, "head")
    assert tables(url) == REGISTRY | AGENTS
    with eng.connect() as c:
        assert c.execute(text("SELECT count(*) FROM model_provider_policies")).scalar_one() == 1
    eng.dispose()

    command.downgrade(cfg, "base")
    assert tables(url) == set()
    command.upgrade(cfg, "head")  # and back up again
    assert tables(url) == REGISTRY | AGENTS


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
