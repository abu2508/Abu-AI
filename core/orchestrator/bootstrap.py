"""Operator bootstrap for the orchestrator's agents. Agents are registered disabled; nothing can run until an
operator explicitly grants permissions, activates a version and enables each agent (this module, reachable only
through the admin CLI / trusted code with an ADMIN actor).

Idempotent and upgrade-safe: an active version whose grants already match what the workflows need is left exactly
as it is; if the needed grants changed (e.g. a new READ tool in a later milestone), a NEW version is created,
granted and activated - the previous version's grants are never edited in place."""

from collections.abc import Iterable, Mapping

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
from core.permissions.tools import ToolRegistry
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


def _matches(v: AgentVersion, agent_id: str, tools: tuple[str, ...], user_memory: bool) -> bool:
    return (
        {a.alias for a in v.model_aliases} == {MOCK_MODEL_ALIAS}
        and {t.tool_name for t in v.tools} == set(tools)
        and all(not t.requires_approval for t in v.tools)
        and {(m.namespace, m.mode) for m in v.memory}
        == {(agent_id, MemoryMode.READ_WRITE)}
        | ({("user", MemoryMode.READ)} if user_memory else set())
    )


async def bootstrap_mock_agents(
    session: AsyncSession,
    actor: Actor,
    registry: WorkflowRegistry | None = None,
    *,
    user_memory_for: Iterable[str] = (),
    limits: agents.VersionLimits | None = None,
    agent_tools: Mapping[str, tuple[str, ...]] | None = None,
    tool_registry: ToolRegistry | None = None,
) -> dict[str, str]:
    """Grant the mock alias + own-namespace memory (+ the agent's READ tools, if any), optionally READ on `user/…`,
    activate and enable. Returns {agent_id: "activated" | "upgraded" | "already_active"}."""
    if actor.type != "admin":
        raise PermissionDeniedError("bootstrapping agents is an operator-only action")
    registry = registry or default_registry()
    tools_by_agent = dict(agent_tools or {})
    user_memory = set(user_memory_for)
    result: dict[str, str] = {}
    await seed_front_desk(session, actor)
    for agent_id in sorted(registry.agent_ids()):
        tools = tuple(tools_by_agent.get(agent_id, ()))
        if tools and tool_registry is None:
            raise ValueError("a tool registry is required to grant tools")
        active = await agents.get_active_version(session, agent_id)
        if active is not None and _matches(active, agent_id, tools, agent_id in user_memory):
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
                max_steps=12,
                timeout_seconds=30,
                max_run_input_tokens=40_000,
                max_run_output_tokens=4_000,
            ),
            actor,
        )
        await agents.grant_model_alias(session, agent_id, number, MOCK_MODEL_ALIAS, actor)
        await agents.grant_memory(session, agent_id, number, agent_id, MemoryMode.READ_WRITE, actor)
        if agent_id in user_memory:
            await agents.grant_memory(session, agent_id, number, "user", MemoryMode.READ, actor)
        for tool in tools:
            if tool_registry is None:  # unreachable: checked above
                raise ValueError("a tool registry is required to grant tools")
            await agents.grant_tool(session, agent_id, number, tool, tool_registry, False, actor)
        await agents.activate_version(session, agent_id, number, actor)
        await agents.set_agent_enabled(session, agent_id, True, actor)
        result[agent_id] = "upgraded" if active is not None else "activated"
    return result
