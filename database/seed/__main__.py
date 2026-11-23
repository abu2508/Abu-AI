"""python -m database.seed  — seeds registry data using ABU_DATABASE_URL."""

import asyncio

from sqlalchemy.ext.asyncio import async_sessionmaker

from database.seed.registry import seed_registry
from services.api.config import get_settings
from services.api.db import create_engine


async def main() -> None:
    engine = create_engine(get_settings())
    if engine is None:
        raise SystemExit("ABU_DATABASE_URL is not set")
    try:
        async with async_sessionmaker(engine)() as session, session.begin():
            await seed_registry(session)
    finally:
        await engine.dispose()
    print("registry seeded")


asyncio.run(main())
