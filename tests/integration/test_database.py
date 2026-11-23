"""Needs the dev stack: see README ("Run integration tests")."""

import os

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

URL = os.environ.get("ABU_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(URL is None, reason="ABU_TEST_DATABASE_URL not set")


async def test_pgvector_available_and_working() -> None:
    assert URL is not None
    engine = create_async_engine(URL)
    try:
        async with engine.connect() as conn:
            ext = await conn.execute(
                text("SELECT extversion FROM pg_extension WHERE extname='vector'")
            )
            assert ext.scalar_one()
            dist = await conn.execute(text("SELECT '[1,2,3]'::vector <-> '[1,2,4]'::vector"))
            assert dist.scalar_one() == 1.0
    finally:
        await engine.dispose()


async def test_litellm_database_is_isolated() -> None:
    """The app role must not be able to connect to LiteLLM's database."""
    assert URL is not None
    bad = URL.rsplit("/", 1)[0] + "/litellm"
    engine = create_async_engine(bad)
    try:
        with pytest.raises(Exception, match="permission denied"):
            async with engine.connect():
                pass
    finally:
        await engine.dispose()
