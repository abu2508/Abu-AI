"""Code-defined tool allowlist. Tool names and input schemas live in code, never in an LLM's
output or in the database: a grant can only reference a registered tool, and a call is
validated against that tool's Pydantic schema before anything runs."""

import asyncio
import enum
import hashlib
import json
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import Actor, record_event, record_event_durable
from core.permissions.errors import (
    ApprovalRequiredError,
    LimitExceededError,
    PermissionDeniedError,
    ToolValidationError,
)
from core.permissions.runtime import RunContext
from database.schemas import ToolApproval

TOOL_NAME = re.compile(r"^[a-z][a-z0-9_]{1,63}(\.[a-z][a-z0-9_]{1,63})?$")
MAX_ARGS_BYTES = 16 * 1024
APPROVAL_TTL = timedelta(minutes=5)


class ToolRisk(enum.IntEnum):
    READ = 0  # no side effects
    WRITE = 1  # changes our own data: always needs a human approval
    IRREVERSIBLE = 2  # payments, sending messages, ...: cannot be granted in Milestone 3


Handler = Callable[[RunContext, Any], Awaitable[Any]]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    input_model: type[BaseModel]
    risk: ToolRisk
    handler: Handler


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if not TOOL_NAME.match(spec.name):
            raise ValueError(f"invalid tool name {spec.name!r}")
        if spec.name in self._tools:
            raise ValueError(f"tool {spec.name!r} already registered")
        if spec.input_model.model_config.get("extra") != "forbid":
            raise ValueError("tool input models must set model_config extra='forbid'")
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)


# Intentionally empty in Milestone 3: no tool exists yet, so nothing can be granted or run.
DEFAULT_TOOLS = ToolRegistry()


def args_hash(tool_name: str, args: dict[str, Any]) -> str:
    canonical = json.dumps({"tool": tool_name, "args": args}, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


async def create_approval(
    session: AsyncSession,
    ctx: RunContext,
    tool_name: str,
    args: dict[str, Any],
    now: datetime | None = None,
) -> uuid.UUID:
    """Called by an authenticated human flow. Binds the approval to these exact arguments."""
    now = now or datetime.now(UTC)
    row = ToolApproval(
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        version_id=ctx.version_id,
        tool_name=tool_name,
        args_hash=args_hash(tool_name, args),
        expires_at=now + APPROVAL_TTL,
    )
    session.add(row)
    await session.flush()
    await record_event(
        session,
        Actor.user(ctx.user_id),
        "approval.created",
        "tool_approval",
        str(row.id),
        {"agent": ctx.agent_id, "tool": tool_name},
    )
    return row.id


class ToolExecutor:
    def __init__(
        self,
        session: AsyncSession,
        audit_factory: async_sessionmaker[AsyncSession],
        registry: ToolRegistry = DEFAULT_TOOLS,
    ) -> None:
        self._session = session
        self._audit = audit_factory
        self._registry = registry

    async def _deny(self, ctx: RunContext, tool: str, reason: str) -> None:
        await record_event_durable(
            self._audit,
            Actor.user(ctx.user_id),
            "tool.denied",
            "agent",
            ctx.agent_id,
            {"tool": tool[:64], "reason": reason},
        )

    async def execute(
        self,
        ctx: RunContext,
        tool_name: str,
        raw_args: dict[str, Any],
        approval_id: uuid.UUID | None = None,
        now: datetime | None = None,
    ) -> Any:
        ctx.begin_step()
        spec = self._registry.get(tool_name)
        try:
            granted_requires_approval = ctx.authorize_tool(tool_name)
        except PermissionDeniedError:
            await self._deny(ctx, tool_name, "not_granted")
            raise
        if spec is None:  # granted in DB but missing from code: fail closed
            await self._deny(ctx, tool_name, "unregistered")
            raise PermissionDeniedError("tool not available")

        if len(json.dumps(raw_args, default=str)) > MAX_ARGS_BYTES:
            await self._deny(ctx, tool_name, "args_too_large")
            raise ToolValidationError("tool arguments too large")
        try:
            parsed = spec.input_model.model_validate(raw_args)
        except ValidationError as exc:
            await self._deny(ctx, tool_name, "schema_validation")
            raise ToolValidationError(f"invalid arguments ({exc.error_count()} errors)") from None

        needs_approval = spec.risk >= ToolRisk.WRITE or granted_requires_approval
        if needs_approval and (
            approval_id is None
            or not await self._consume(ctx, tool_name, raw_args, approval_id, now)
        ):
            await self._deny(ctx, tool_name, "approval_required")
            raise ApprovalRequiredError("a valid approval for these exact arguments is required")

        try:
            async with asyncio.timeout(ctx.remaining_seconds()):
                result = await spec.handler(ctx, parsed)
        except TimeoutError:
            raise LimitExceededError("run timeout exceeded") from None
        await record_event_durable(
            self._audit,
            Actor.user(ctx.user_id),
            "tool.executed",
            "agent",
            ctx.agent_id,
            {"tool": tool_name, "risk": spec.risk.name.lower()},
        )
        return result

    async def _consume(
        self,
        ctx: RunContext,
        tool_name: str,
        args: dict[str, Any],
        approval_id: uuid.UUID,
        now: datetime | None,
    ) -> bool:
        """Atomically burn a matching, unexpired, unused approval. One approval = one call."""
        now = now or datetime.now(UTC)
        result = await self._session.execute(
            update(ToolApproval)
            .where(
                ToolApproval.id == approval_id,
                ToolApproval.user_id == ctx.user_id,
                ToolApproval.agent_id == ctx.agent_id,
                ToolApproval.version_id == ctx.version_id,
                ToolApproval.tool_name == tool_name,
                ToolApproval.args_hash == args_hash(tool_name, args),
                ToolApproval.used_at.is_(None),
                ToolApproval.expires_at > now,
            )
            .values(used_at=now)
        )
        return bool(result.rowcount)  # type: ignore[attr-defined]
