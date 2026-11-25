"""Alembic environment. The URL is, in order: a caller-set sqlalchemy.url, ABU_MIGRATION_DATABASE_URL (the OWNER role used
by the bootstrap step), then ABU_DATABASE_URL (single-role development and CI)."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import create_engine

import database.schemas  # noqa: F401  (registers tables on Base.metadata)
from database.schemas.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name, disable_existing_loggers=False)

target_metadata = Base.metadata


def _url() -> str:
    url = (
        config.get_main_option("sqlalchemy.url")
        or os.environ.get("ABU_MIGRATION_DATABASE_URL")
        or os.environ.get("ABU_DATABASE_URL")
    )
    if not url:
        raise RuntimeError(
            "Set ABU_MIGRATION_DATABASE_URL (owner role) or ABU_DATABASE_URL (postgresql+psycopg://...) to run migrations."
        )
    return url


def run_migrations_online() -> None:
    engine = create_engine(_url())
    with engine.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()
    engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline migrations are not supported.")
run_migrations_online()
