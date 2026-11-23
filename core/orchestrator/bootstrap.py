"""Operator bootstrap for the MOCK workflows. Agents are registered disabled; nothing can run until an
operator explicitly grants permissions, activates a version and enables each agent (this module,
reachable only through the admin CLI / trusted code with an ADMIN actor). Idempotent: an agent that is
already active is left exactly as it is."""

from collections.abc import Iterable

from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_registry import service as agents
from core.audit.service import Actor
from core.model_registry.errors import NotFoundError
from core.orchestrator.workflows import (
    FRONT_DESK_AGENT,
    MOCK_MODEL_ALIAS,
    WorkflowRegistry,
    default_registry,
)
from core.permissions.errors import PermissionDeniedError
from database.schemas import AgentVersion, MemoryMode, VersionState

DESCRIPTION = (
    "Answers general requests and orchestrator-level replies (clarification, unsupported)."
)


async def seed_front_desk(session: AsyncSession, actor: Actor) -> None:
    """Register the fourth agent (disabled, empty draft v1). Idempotent."""
    try:
        await agents.get_agent(session, FRONT_DESK_AGENT)
    except NotFoundError:
        await agents.register_agent(
            session, FRONT_DESK_AGENT, "General Assistant", DESCRIPTION, actor
        )


async def bootstrap_mock_agents(
    session: AsyncSession,
    actor: Actor,
    registry: WorkflowRegistry | None = None,
    *,
    user_memory_for: Iterable[str] = (),
    limits: agents.VersionLimits | None = None,
) -> dict[str, str]:
    """Grant the mock alias + own-namespace memory, optionally READ access to `user/…` for the named
    agents, activate and enable. Returns {agent_id: "activated" | "already_active"}."""
    if actor.type != "admin":
        raise PermissionDeniedError("bootstrapping agents is an operator-only action")
    registry = registry or default_registry()
    user_memory = set(user_memory_for)
    result: dict[str, str] = {}
    await seed_front_desk(session, actor)
    for agent_id in sorted(registry.agent_ids()):
        if await agents.get_active_version(session, agent_id) is not None:
            await agents.set_agent_enabled(session, agent_id, True, actor)
            result[agent_id] = "already_active"
            continue
        versions = await agents.list_versions(session, agent_id)
        draft: AgentVersion = (
            versions[-1]
            if versions and versions[-1].state is VersionState.DRAFT
            else await agents.create_version(session, agent_id, agents.VersionLimits(), actor)
        )
        number = draft.version
        await agents.update_limits(
            session,
            agent_id,
            number,
            limits
            or agents.VersionLimits(
                max_steps=8,
                timeout_seconds=30,
                max_run_input_tokens=20_000,
                max_run_output_tokens=4_000,
            ),
            actor,
        )
        await agents.grant_model_alias(session, agent_id, number, MOCK_MODEL_ALIAS, actor)
        await agents.grant_memory(session, agent_id, number, agent_id, MemoryMode.READ_WRITE, actor)
        if agent_id in user_memory:
            await agents.grant_memory(session, agent_id, number, "user", MemoryMode.READ, actor)
        await agents.activate_version(session, agent_id, number, actor)
        await agents.set_agent_enabled(session, agent_id, True, actor)
        result[agent_id] = "activated"
    return result
