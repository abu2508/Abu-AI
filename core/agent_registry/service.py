"""Agent Registry: agents, versions with a lifecycle, and per-version default-deny permissions.

Lifecycle:  DRAFT --activate--> ACTIVE --retire--> RETIRED
  * Only a DRAFT can be edited (limits, config, grants). ACTIVE and RETIRED versions are frozen,
    so the audit trail always matches what actually ran. To change an active agent: clone the
    version into a new DRAFT, edit it, activate it (the old one is retired atomically).
  * Roll back = clone an old version and activate the clone.
  * At most one ACTIVE version per agent (database partial unique index).
  * An agent only runs when `enabled` AND it has an ACTIVE version.
  * Every state change is written to the append-only audit log.

Privilege-expanding operations (enable, activate, grant_*) are NOT exposed through the app API;
they are administrator actions (CLI) in this milestone.
"""

import re
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.service import SYSTEM, Actor, record_event
from core.model_registry.errors import (
    ApprovalError,
    DuplicateError,
    NotFoundError,
    VersionStateError,
)
from core.permissions.errors import PermissionDeniedError
from core.permissions.tools import ToolRegistry, ToolRisk
from database.schemas import (
    Agent,
    AgentVersion,
    AgentVersionMemory,
    AgentVersionModelAlias,
    AgentVersionTool,
    MemoryMode,
    ModelRoute,
    VersionState,
)

NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(/[a-z0-9][a-z0-9_.:-]*)*$")
AGENT_ID = re.compile(r"^[a-z][a-z0-9_]{2,63}$")
MAX_CONFIG_BYTES = 8 * 1024


class VersionLimits(BaseModel):
    """Hard ceilings are enforced here AND by database CHECK constraints."""

    model_config = ConfigDict(extra="forbid")

    max_steps: int = Field(default=8, ge=1, le=50)
    timeout_seconds: int = Field(default=60, ge=1, le=300)
    max_run_input_tokens: int = Field(default=20_000, ge=1, le=200_000)
    max_run_output_tokens: int = Field(default=4_000, ge=1, le=100_000)
    # Per-run paid-spend cap in INR. 0 (default) means free models only.
    max_run_cost_inr: Decimal = Field(default=Decimal(0), ge=0, le=50)
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("config")
    @classmethod
    def _small(cls, v: dict[str, Any]) -> dict[str, Any]:
        import json

        if len(json.dumps(v)) > MAX_CONFIG_BYTES:
            raise ValueError("config too large")
        return v


def _apply(version: AgentVersion, limits: VersionLimits) -> None:
    version.max_steps = limits.max_steps
    version.timeout_seconds = limits.timeout_seconds
    version.max_run_input_tokens = limits.max_run_input_tokens
    version.max_run_output_tokens = limits.max_run_output_tokens
    version.max_run_cost_inr = limits.max_run_cost_inr
    version.config = limits.config


# --- agents -----------------------------------------------------------------------------


async def get_agent(session: AsyncSession, agent_id: str) -> Agent:
    agent = await session.get(Agent, agent_id, populate_existing=True)
    if agent is None:
        raise NotFoundError(f"no agent {agent_id!r}")
    return agent


async def register_agent(
    session: AsyncSession, agent_id: str, name: str, description: str, actor: Actor = SYSTEM
) -> Agent:
    """Creates the agent DISABLED with an empty DRAFT version 1 (no permissions)."""
    if not AGENT_ID.match(agent_id):
        raise ValueError("agent_id must be a lowercase slug")
    if await session.get(Agent, agent_id):
        raise DuplicateError(f"agent {agent_id!r} already exists")
    agent = Agent(agent_id=agent_id, name=name, description=description, enabled=False)
    session.add(agent)
    await session.flush()
    await record_event(session, actor, "agent.registered", "agent", agent_id)
    await create_version(session, agent_id, VersionLimits(), actor)
    return agent


async def set_agent_enabled(
    session: AsyncSession, agent_id: str, enabled: bool, actor: Actor = SYSTEM
) -> Agent:
    agent = await get_agent(session, agent_id)
    if enabled and await get_active_version(session, agent_id) is None:
        raise ApprovalError(f"cannot enable {agent_id!r}: it has no active version")
    if agent.enabled != enabled:
        agent.enabled = enabled
        await session.flush()
        await record_event(
            session, actor, "agent.enabled" if enabled else "agent.disabled", "agent", agent_id
        )
    return agent


# --- versions ---------------------------------------------------------------------------


async def get_version(session: AsyncSession, agent_id: str, number: int) -> AgentVersion:
    version = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id, AgentVersion.version == number)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if version is None:
        raise NotFoundError(f"no version {number} of agent {agent_id!r}")
    return version


async def get_active_version(session: AsyncSession, agent_id: str) -> AgentVersion | None:
    return (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id, AgentVersion.state == VersionState.ACTIVE)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def list_versions(session: AsyncSession, agent_id: str) -> list[AgentVersion]:
    rows = await session.execute(
        select(AgentVersion)
        .where(AgentVersion.agent_id == agent_id)
        .order_by(AgentVersion.version)
        .execution_options(populate_existing=True)
    )
    return list(rows.scalars())


async def create_version(
    session: AsyncSession, agent_id: str, limits: VersionLimits, actor: Actor = SYSTEM
) -> AgentVersion:
    await get_agent(session, agent_id)
    latest = (
        await session.execute(
            select(func.coalesce(func.max(AgentVersion.version), 0)).where(
                AgentVersion.agent_id == agent_id
            )
        )
    ).scalar_one()
    version = AgentVersion(agent_id=agent_id, version=latest + 1, state=VersionState.DRAFT)
    _apply(version, limits)
    session.add(version)
    await session.flush()
    # Eager-load the (empty) permission collections: lazy loads are illegal in async context.
    await session.refresh(version, ["model_aliases", "tools", "memory"])
    await record_event(
        session, actor, "agent.version_created", "agent_version", f"{agent_id}:{version.version}"
    )
    return version


async def clone_version(
    session: AsyncSession, agent_id: str, number: int, actor: Actor = SYSTEM
) -> AgentVersion:
    src = await get_version(session, agent_id, number)
    new = await create_version(
        session,
        agent_id,
        VersionLimits(
            max_steps=src.max_steps,
            timeout_seconds=src.timeout_seconds,
            max_run_input_tokens=src.max_run_input_tokens,
            max_run_output_tokens=src.max_run_output_tokens,
            max_run_cost_inr=Decimal(src.max_run_cost_inr),
            config=dict(src.config),
        ),
        actor,
    )
    for a in src.model_aliases:
        new.model_aliases.append(AgentVersionModelAlias(alias=a.alias))
    for t in src.tools:
        new.tools.append(
            AgentVersionTool(tool_name=t.tool_name, requires_approval=t.requires_approval)
        )
    for m in src.memory:
        new.memory.append(AgentVersionMemory(namespace=m.namespace, mode=m.mode))
    await session.flush()
    await record_event(
        session,
        actor,
        "agent.version_cloned",
        "agent_version",
        f"{agent_id}:{new.version}",
        {"from": number},
    )
    return new


def _require_draft(version: AgentVersion) -> None:
    if version.state is not VersionState.DRAFT:
        raise VersionStateError(
            f"version {version.agent_id}:{version.version} is {version.state.value}; "
            "only drafts are editable (clone it to change)"
        )


async def update_limits(
    session: AsyncSession,
    agent_id: str,
    number: int,
    limits: VersionLimits,
    actor: Actor = SYSTEM,
) -> AgentVersion:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    _apply(version, limits)
    await session.flush()
    await record_event(
        session, actor, "agent.limits_updated", "agent_version", f"{agent_id}:{number}"
    )
    return version


async def activate_version(
    session: AsyncSession, agent_id: str, number: int, actor: Actor = SYSTEM
) -> AgentVersion:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    current = await get_active_version(session, agent_id)
    now = datetime.now(UTC)
    if current is not None:
        current.state = VersionState.RETIRED
        current.retired_at = now
        await session.flush()  # free the one-active slot before activating
        await record_event(
            session,
            actor,
            "agent.version_retired",
            "agent_version",
            f"{agent_id}:{current.version}",
        )
    version.state = VersionState.ACTIVE
    version.activated_at = now
    await session.flush()
    await record_event(
        session, actor, "agent.version_activated", "agent_version", f"{agent_id}:{number}"
    )
    return version


async def retire_version(
    session: AsyncSession, agent_id: str, number: int, actor: Actor = SYSTEM
) -> AgentVersion:
    """Retiring the active version also DISABLES the agent: it cannot run without one."""
    version = await get_version(session, agent_id, number)
    if version.state is not VersionState.ACTIVE:
        raise VersionStateError("only the active version can be retired")
    version.state = VersionState.RETIRED
    version.retired_at = datetime.now(UTC)
    await session.flush()
    await record_event(
        session, actor, "agent.version_retired", "agent_version", f"{agent_id}:{number}"
    )
    await set_agent_enabled(session, agent_id, False, actor)
    return version


# --- grants (draft only, default-deny) --------------------------------------------------


async def grant_model_alias(
    session: AsyncSession, agent_id: str, number: int, alias: str, actor: Actor = SYSTEM
) -> None:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    exists = (
        await session.execute(select(ModelRoute.id).where(ModelRoute.alias == alias).limit(1))
    ).scalar_one_or_none()
    if exists is None:
        raise NotFoundError(f"no model route with alias {alias!r}")
    if all(a.alias != alias for a in version.model_aliases):
        version.model_aliases.append(AgentVersionModelAlias(alias=alias))
        await session.flush()
        await record_event(
            session,
            actor,
            "agent.permission_granted",
            "agent_version",
            f"{agent_id}:{number}",
            {"kind": "model_alias", "value": alias},
        )


async def grant_tool(
    session: AsyncSession,
    agent_id: str,
    number: int,
    tool_name: str,
    registry: ToolRegistry,
    requires_approval: bool = True,
    actor: Actor = SYSTEM,
) -> None:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    spec = registry.get(tool_name)
    if spec is None:
        raise NotFoundError(f"no registered tool {tool_name!r}")  # only code-defined tools exist
    if spec.risk >= ToolRisk.IRREVERSIBLE:
        raise ApprovalError(
            f"tool {tool_name!r} is irreversible (payments, sending, ...) and cannot be granted yet"
        )
    needs = requires_approval or spec.risk >= ToolRisk.WRITE  # WRITE can never skip approval
    existing = next((t for t in version.tools if t.tool_name == tool_name), None)
    if existing is None:
        version.tools.append(AgentVersionTool(tool_name=tool_name, requires_approval=needs))
    else:
        existing.requires_approval = needs
    await session.flush()
    await record_event(
        session,
        actor,
        "agent.permission_granted",
        "agent_version",
        f"{agent_id}:{number}",
        {"kind": "tool", "value": tool_name, "requires_approval": needs},
    )


def validate_namespace(agent_id: str, namespace: str) -> None:
    if len(namespace) > 128 or not NAMESPACE.match(namespace) or ".." in namespace:
        raise ValueError(f"invalid memory namespace {namespace!r}")
    if namespace.split("/", 1)[0] != agent_id:
        raise PermissionDeniedError(
            f"namespace {namespace!r} does not belong to agent {agent_id!r}: cross-agent memory access is not allowed"
        )


async def grant_memory(
    session: AsyncSession,
    agent_id: str,
    number: int,
    namespace: str,
    mode: MemoryMode,
    actor: Actor = SYSTEM,
) -> None:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    validate_namespace(agent_id, namespace)
    existing = next((m for m in version.memory if m.namespace == namespace), None)
    if existing is None:
        version.memory.append(AgentVersionMemory(namespace=namespace, mode=mode))
    else:
        existing.mode = mode
    await session.flush()
    await record_event(
        session,
        actor,
        "agent.permission_granted",
        "agent_version",
        f"{agent_id}:{number}",
        {"kind": "memory", "value": namespace, "mode": mode.value},
    )


async def revoke(
    session: AsyncSession,
    agent_id: str,
    number: int,
    kind: str,
    value: str,
    actor: Actor = SYSTEM,
) -> None:
    version = await get_version(session, agent_id, number)
    _require_draft(version)
    if kind == "model_alias":
        version.model_aliases = [a for a in version.model_aliases if a.alias != value]
    elif kind == "tool":
        version.tools = [t for t in version.tools if t.tool_name != value]
    elif kind == "memory":
        version.memory = [m for m in version.memory if m.namespace != value]
    else:
        raise ValueError(f"unknown permission kind {kind!r}")
    await session.flush()
    await record_event(
        session,
        actor,
        "agent.permission_revoked",
        "agent_version",
        f"{agent_id}:{number}",
        {"kind": kind, "value": value},
    )
