"""Health endpoints.

/health        liveness: process is up. No dependencies are touched.
/health/ready  readiness: PostgreSQL reachable, pgvector installed, LiteLLM alive.

Failure details are deliberately generic: no hostnames, URLs or credentials leak.
"""

import logging
from typing import Literal

import httpx
from fastapi import APIRouter, Request, Response, status
from pydantic import BaseModel
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from services.api.config import Settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["health"])

CheckStatus = Literal["ok", "fail", "unconfigured"]


class Liveness(BaseModel):
    status: Literal["ok"] = "ok"


class Readiness(BaseModel):
    status: Literal["ready", "degraded"]
    checks: dict[str, CheckStatus]


@router.get("/health", response_model=Liveness)
async def health() -> Liveness:
    return Liveness()


async def check_database(engine: AsyncEngine | None) -> CheckStatus:
    if engine is None:
        return "unconfigured"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return "ok"
    except Exception:
        logger.warning("database readiness check failed", exc_info=True)
        return "fail"


async def check_pgvector(engine: AsyncEngine | None) -> CheckStatus:
    if engine is None:
        return "unconfigured"
    try:
        async with engine.connect() as conn:
            row = await conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'"))
            return "ok" if row.first() else "fail"
    except Exception:
        logger.warning("pgvector readiness check failed", exc_info=True)
        return "fail"


async def check_litellm(settings: Settings) -> CheckStatus:
    # /health/liveliness is LiteLLM's unauthenticated liveness probe; it makes no model calls.
    url = f"{settings.litellm_base_url.rstrip('/')}/health/liveliness"
    try:
        async with httpx.AsyncClient(timeout=settings.dependency_timeout_seconds) as client:
            resp = await client.get(url)
        return "ok" if resp.status_code == 200 else "fail"
    except Exception:
        logger.warning("litellm readiness check failed", exc_info=True)
        return "fail"


@router.get("/health/ready", response_model=Readiness)
async def ready(request: Request, response: Response) -> Readiness:
    settings: Settings = request.app.state.settings
    engine: AsyncEngine | None = request.app.state.engine
    checks: dict[str, CheckStatus] = {
        "database": await check_database(engine),
        "pgvector": await check_pgvector(engine),
        "litellm": await check_litellm(settings),
    }
    ok = all(v == "ok" for v in checks.values())
    if not ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return Readiness(status="ready" if ok else "degraded", checks=checks)
