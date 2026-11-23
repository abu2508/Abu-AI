"""Abu AI API entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from services.api import health
from services.api.config import Settings, get_settings
from services.api.db import create_engine


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        try:
            yield
        finally:
            if app.state.engine is not None:
                await app.state.engine.dispose()

    app = FastAPI(
        title="Abu AI",
        version="0.1.0",
        lifespan=lifespan,
        # Interactive docs are development-only.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.engine = None
    app.include_router(health.router)
    return app


app = create_app()
