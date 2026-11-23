"""Integration fixtures: a throwaway database per test, built by the REAL Alembic migrations.

Needs ABU_TEST_ADMIN_URL: a sync-driver URL with CREATEDB rights, e.g.
postgresql+psycopg://abu_admin:***@127.0.0.1:5432/postgres  (see README).
"""

import os
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from dataclasses import dataclass

import httpx
import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.governance.service import Governance
from services.api.config import Settings
from services.api.main import create_app
from tests.integration.helpers import Device, enroll_device, make_settings, make_user

ADMIN_URL = os.environ.get("ABU_TEST_ADMIN_URL")


def alembic_config(url: str | URL) -> Config:
    cfg = Config("alembic.ini")
    rendered = make_url(url).render_as_string(hide_password=False).replace("%", "%%")
    cfg.set_main_option("sqlalchemy.url", rendered)
    return cfg


@pytest.fixture
def scratch_db() -> Iterator[Callable[[], URL]]:
    """Factory returning the URL of a fresh, empty database; all are dropped afterwards."""
    if ADMIN_URL is None:
        pytest.skip("ABU_TEST_ADMIN_URL not set")
    admin = create_engine(ADMIN_URL, isolation_level="AUTOCOMMIT")
    created: list[str] = []

    def make() -> URL:
        name = f"abu_test_{uuid.uuid4().hex[:12]}"
        with admin.connect() as c:
            c.execute(text(f'CREATE DATABASE "{name}"'))
        created.append(name)
        return make_url(ADMIN_URL).set(database=name)

    yield make
    with admin.connect() as c:
        for name in created:
            c.execute(text(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)'))
    admin.dispose()


@pytest.fixture
def migrated_url(scratch_db: Callable[[], URL]) -> URL:
    url = scratch_db()
    command.upgrade(alembic_config(url), "head")
    return url


@pytest.fixture
async def sessions(migrated_url: URL) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    engine = create_async_engine(migrated_url)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


@pytest.fixture
async def session(sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    async with sessions() as s:
        yield s


@pytest.fixture
async def gov(sessions: async_sessionmaker[AsyncSession]) -> Governance:
    return Governance(sessions)


@dataclass
class Api:
    app: FastAPI
    client: httpx.AsyncClient
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]


@pytest.fixture
async def api(migrated_url: URL, sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[Api]:
    settings = make_settings(migrated_url)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield Api(app, client, settings, sessions)


@pytest.fixture
async def device(api: Api) -> Device:
    uid = await make_user(api.sessions)
    return await enroll_device(api.sessions, api.settings, api.client, uid)
