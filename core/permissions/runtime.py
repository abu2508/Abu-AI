"""RunContext: the server-side, per-execution capability object.

Built ONLY from the database state of an ENABLED agent's ACTIVE version. Everything an agent
may do during a run is checked against it, on every call: model aliases, tools, memory
namespaces, step count, wall-clock deadline, token and cost budgets. Default-deny throughout.
"""

import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.governance.errors import GovernanceError
from core.governance.service import Governance
from core.permissions.errors import (
    AgentNotPermittedError,
    LimitExceededError,
    PermissionDeniedError,
)
from database.schemas import Agent, AgentVersion, MemoryMode, User, VersionState


@dataclass(frozen=True)
class RunLimits:
    max_steps: int
    timeout_seconds: int
    max_input_tokens: int
    max_output_tokens: int
    max_cost_inr: Decimal


def estimate_cost_inr(
    input_tokens: int,
    output_tokens: int,
    input_per_million_usd: Decimal,
    output_per_million_usd: Decimal,
    usd_inr_rate: Decimal,
) -> Decimal:
    usd = (input_tokens * input_per_million_usd + output_tokens * output_per_million_usd) / Decimal(
        1_000_000
    )
    return usd * usd_inr_rate


def namespace_owner(namespace: str) -> str:
    return namespace.split("/", 1)[0]


@dataclass
class RunContext:
    user_id: uuid.UUID
    agent_id: str
    version_id: uuid.UUID
    version: int
    limits: RunLimits
    model_aliases: frozenset[str]
    tools: dict[str, bool]  # tool name -> requires_approval
    memory: dict[str, MemoryMode]  # namespace -> mode
    execution_id: uuid.UUID = field(default_factory=uuid.uuid4)
    trace_id: str = ""
    task_type: str = "general"
    clock: Callable[[], float] = field(default=time.monotonic, repr=False)
    started_at: float = 0.0
    steps_used: int = 0
    input_tokens_used: int = 0
    output_tokens_used: int = 0
    cost_inr_used: Decimal = Decimal(0)

    def __post_init__(self) -> None:
        self.started_at = self.clock()

    # --- time and steps -------------------------------------------------------------
    def remaining_seconds(self) -> float:
        left = self.limits.timeout_seconds - (self.clock() - self.started_at)
        if left <= 0:
            raise LimitExceededError("run timeout exceeded")
        return left

    def begin_step(self) -> None:
        self.remaining_seconds()
        if self.steps_used >= self.limits.max_steps:
            raise LimitExceededError("maximum execution steps reached")
        self.steps_used += 1

    # --- budgets --------------------------------------------------------------------
    @property
    def remaining_input_tokens(self) -> int:
        return max(0, self.limits.max_input_tokens - self.input_tokens_used)

    @property
    def remaining_output_tokens(self) -> int:
        return max(0, self.limits.max_output_tokens - self.output_tokens_used)

    @property
    def remaining_cost_inr(self) -> Decimal:
        return max(Decimal(0), self.limits.max_cost_inr - self.cost_inr_used)

    def record_usage(self, input_tokens: int, output_tokens: int, cost_inr: Decimal) -> None:
        self.input_tokens_used += input_tokens
        self.output_tokens_used += output_tokens
        self.cost_inr_used += cost_inr

    # --- permissions (default-deny) -------------------------------------------------
    def authorize_alias(self, alias: str) -> None:
        if alias not in self.model_aliases:
            raise PermissionDeniedError(f"agent may not use model alias {alias!r}")

    def authorize_tool(self, name: str) -> bool:
        """Returns whether the grant itself demands approval."""
        if name not in self.tools:
            raise PermissionDeniedError("tool not granted")
        return self.tools[name]

    def authorize_memory(self, namespace: str, *, write: bool = False) -> None:
        # Cross-agent isolation holds even if a bad grant row existed.
        # Allowed roots: the agent's own id, or the user-level "user" root. The grant check below
        # still decides - "user/..." is reachable ONLY where the version was explicitly granted it.
        if namespace_owner(namespace) not in (self.agent_id, "user") or ".." in namespace:
            raise PermissionDeniedError("memory namespace belongs to another agent")
        for granted, mode in self.memory.items():
            if namespace == granted or namespace.startswith(granted + "/"):
                if write and mode is not MemoryMode.READ_WRITE:
                    raise PermissionDeniedError("memory namespace is read-only")
                return
        raise PermissionDeniedError("memory namespace not granted")


async def start_run(
    session: AsyncSession,
    *,
    user_id: uuid.UUID,
    agent_id: str,
    governance: Governance,
    task_type: str = "general",
    trace_id: str | None = None,
) -> RunContext:
    """The only way to get a RunContext. Order matters:
      1. kill switch (fail closed) - nothing else is even consulted if execution is blocked;
      2. unknown/disabled agent or user, or no ACTIVE version => refused;
      3. an execution record is created (own transaction) and its id travels in the context.
    Queued or scheduled work must call this at dequeue time, so an active kill switch stops it."""
    try:
        await governance.require_execution_allowed()
    except GovernanceError as exc:
        await governance.audit_blocked(user_id, agent_id, "blocked", type(exc).__name__)
        raise
    ctx = await _load_context(session, user_id=user_id, agent_id=agent_id)
    execution_id, trace = await governance.begin(
        user_id=user_id,
        agent_id=agent_id,
        version_id=ctx.version_id,
        version=ctx.version,
        task_type=task_type,
        trace_id=trace_id,
    )
    ctx.execution_id, ctx.trace_id, ctx.task_type = execution_id, trace, task_type
    return ctx


async def _load_context(session: AsyncSession, *, user_id: uuid.UUID, agent_id: str) -> RunContext:
    """Refuses unknown/disabled agents and agents with no ACTIVE version; permissions are read
    fresh from the database on every run."""
    user = await session.get(User, user_id)
    if user is None or user.disabled_at is not None:
        raise AgentNotPermittedError("unknown user")
    agent = await session.get(Agent, agent_id, populate_existing=True)
    if agent is None or not agent.enabled:
        raise AgentNotPermittedError(f"agent {agent_id!r} is unknown or disabled")
    version = (
        await session.execute(
            select(AgentVersion)
            .where(AgentVersion.agent_id == agent_id, AgentVersion.state == VersionState.ACTIVE)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if version is None:
        raise AgentNotPermittedError(f"agent {agent_id!r} has no active version")
    return RunContext(
        user_id=user_id,
        agent_id=agent_id,
        version_id=version.id,
        version=version.version,
        limits=RunLimits(
            version.max_steps,
            version.timeout_seconds,
            version.max_run_input_tokens,
            version.max_run_output_tokens,
            Decimal(version.max_run_cost_inr),
        ),
        model_aliases=frozenset(a.alias for a in version.model_aliases),
        tools={t.tool_name: t.requires_approval for t in version.tools},
        memory={m.namespace: m.mode for m in version.memory},
    )
