"""Abu AI API entrypoint. Run with:  uvicorn --factory services.api.main:create_app"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from core.governance.service import Governance
from core.memory.runtime import build_keyring, open_runtime, verify_embedding_dimension
from core.orchestrator.orchestrator import Orchestrator
from services.api import agents, finance, governance, health, memory, orchestrator
from services.api.auth import routes as auth_routes
from services.api.config import Settings, get_settings
from services.api.db import create_engine, create_sessionmaker
from services.api.middleware import (
    BodySizeLimitMiddleware,
    HardeningHeadersMiddleware,
    RateLimitMiddleware,
)
from services.api.ratelimit import RateLimiter


def create_app(settings: Settings | None = None, limiter: RateLimiter | None = None) -> FastAPI:
    settings = settings or get_settings()
    settings.require_auth_secrets()  # fail fast: never start without (distinct, strong) auth secrets
    limiter = limiter or RateLimiter()
    if settings.database_url is not None:
        build_keyring(settings)  # fail fast: no memory key material => no start

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.engine = create_engine(settings)
        app.state.sessionmaker = create_sessionmaker(app.state.engine)
        app.state.memory = None
        app.state.orchestrator = None
        try:
            if settings.database_url is not None and app.state.sessionmaker is not None:
                app.state.memory = await open_runtime(settings)
                async with app.state.sessionmaker() as s:
                    await verify_embedding_dimension(s, app.state.memory.embedder)
                app.state.orchestrator = Orchestrator(
                    app.state.sessionmaker,
                    Governance(app.state.sessionmaker),
                    app.state.memory,
                    token_hash_key=settings.token_hash_key,  # type: ignore[arg-type]
                    response_ttl_hours=settings.orchestrator_response_ttl_hours,
                    thread_days=settings.retention_thread_days,
                )
            yield
        finally:
            if app.state.memory is not None:
                await app.state.memory.close()
            if app.state.engine is not None:
                await app.state.engine.dispose()

    app = FastAPI(
        title="Abu AI",
        version="0.6.0",
        lifespan=lifespan,
        # Interactive docs are development-only.
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )
    app.state.settings = settings
    app.state.limiter = limiter
    app.state.engine = None
    app.state.sessionmaker = None
    app.state.memory = None
    app.state.orchestrator = None
    app.include_router(health.router)
    app.include_router(auth_routes.router)
    app.include_router(agents.router)
    app.include_router(governance.router)
    app.include_router(memory.router)
    app.include_router(orchestrator.router)
    app.include_router(finance.router)

    # Added last = outermost: hardening headers wrap everything, then size limit, then rate limit.
    app.add_middleware(
        RateLimitMiddleware,
        limiter=limiter,
        trusted_hops=settings.trusted_proxy_hops,
        per_minute=settings.rate_limit_ip_per_minute,
        auth_per_minute=settings.rate_limit_auth_per_minute,
    )
    app.add_middleware(
        BodySizeLimitMiddleware,
        max_bytes=settings.max_request_bytes,
        path_limits={
            "/v1/finance/import": settings.max_import_bytes * 4 // 3 + 1024
        },  # base64 overhead
    )
    app.add_middleware(HardeningHeadersMiddleware)
    return app
