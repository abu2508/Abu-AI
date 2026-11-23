"""Async database engine management."""

from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from services.api.config import Settings


def create_engine(settings: Settings) -> AsyncEngine | None:
    if settings.database_url is None:
        return None
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=5,
        max_overflow=5,
        pool_pre_ping=True,
        connect_args={"timeout": settings.dependency_timeout_seconds},
    )
