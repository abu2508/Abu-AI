"""Agent Registry foundations (Milestone 2). Versioning, tool/memory permissions and limits
arrive in Milestone 3. Agents are created DISABLED; enabling is an explicit operator action."""

from sqlalchemy.ext.asyncio import AsyncSession

from core.model_registry.errors import DuplicateError, NotFoundError
from database.schemas import Agent, AgentModelPermission


async def register_agent(
    session: AsyncSession, agent_id: str, name: str, description: str
) -> Agent:
    if await session.get(Agent, agent_id):
        raise DuplicateError(f"agent {agent_id!r} already exists")
    agent = Agent(agent_id=agent_id, name=name, description=description, enabled=False)
    session.add(agent)
    await session.flush()
    return agent


async def set_agent_enabled(session: AsyncSession, agent_id: str, enabled: bool) -> Agent:
    agent = await session.get(Agent, agent_id)
    if agent is None:
        raise NotFoundError(f"no agent {agent_id!r}")
    agent.enabled = enabled
    await session.flush()
    return agent


async def grant_model_alias(session: AsyncSession, agent_id: str, alias: str) -> None:
    if await session.get(Agent, agent_id) is None:
        raise NotFoundError(f"no agent {agent_id!r}")
    if await session.get(AgentModelPermission, (agent_id, alias)) is None:
        session.add(AgentModelPermission(agent_id=agent_id, route_alias=alias))
        await session.flush()
