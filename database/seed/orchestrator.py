"""Seed the orchestrator's front-desk agent (registered DISABLED, like the other agents). Kept apart
from seed_registry so the three mandatory agents seeded by Milestone 2 are unchanged."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.service import SYSTEM
from core.orchestrator.bootstrap import seed_front_desk


async def seed_orchestrator(session: AsyncSession) -> None:
    await seed_front_desk(session, SYSTEM)
