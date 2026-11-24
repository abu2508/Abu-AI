# mypy: disable-error-code="no-untyped-def,arg-type"
"""B2 with a real database: the dev schema route works only with a valid session."""

from collections.abc import AsyncIterator

import httpx
import pytest
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from services.api.main import create_app
from tests.integration.helpers import Device, enroll_device, make_settings, make_user


@pytest.fixture
async def dev_api(
    migrated_url: URL, sessions: async_sessionmaker[AsyncSession]
) -> AsyncIterator[tuple[httpx.AsyncClient, Device]]:
    settings = make_settings(migrated_url, env="test", openapi_mode="authenticated")
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            device = await enroll_device(sessions, settings, client, await make_user(sessions))
            yield client, device


async def test_schema_needs_a_valid_session(dev_api) -> None:
    client, device = dev_api
    assert (await client.get("/v1/dev/openapi.json")).status_code == 401
    ok = await client.get("/v1/dev/openapi.json", headers=device.headers)
    assert (
        ok.status_code == 200
        and ok.json()["info"]["title"] == "Abu AI"
        and "/v1/finance/summary" in ok.json()["paths"]
    )
    for public in ("/openapi.json", "/docs", "/redoc"):
        assert (await client.get(public)).status_code == 404


async def test_revoked_session_loses_access(dev_api) -> None:
    client, device = dev_api
    assert (await client.post("/v1/auth/logout", headers=device.headers)).status_code in (200, 204)
    assert (await client.get("/v1/dev/openapi.json", headers=device.headers)).status_code == 401
