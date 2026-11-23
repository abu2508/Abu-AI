"""Async database engine management."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from services.api.config import Settings


def create_engine(settings: Settings) -> AsyncEngine | None:
    if settings.database_url is None:
        return None
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={"connect_timeout": max(1, int(settings.dependency_timeout_seconds))},
    )


def create_sessionmaker(engine: AsyncEngine | None) -> async_sessionmaker[AsyncSession] | None:
    return None if engine is None else async_sessionmaker(engine, expire_on_commit=False)
