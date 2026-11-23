"""Governance facade. Every execution entry point (start_run, ModelGateway, ToolExecutor) takes a
REQUIRED Governance so that no code path can skip the kill switch by omission."""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import SYSTEM, record_event_durable
from core.governance import executions, killswitch
from core.governance.spending import SpendController, SpendingSnapshot


@dataclass(frozen=True)
class Policy:
    """A point-in-time view used to decide routing for ONE model call."""

    paid_allowed: bool
    paid_block_reason: str | None
    spending: SpendingSnapshot


class Governance:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.factory = factory
        self.clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))
        self.spend = SpendController(factory, self.clock)

    async def require_execution_allowed(self) -> None:
        """Raises KillSwitchActiveError, or GovernanceUnavailableError (fail closed)."""
        await killswitch.require_inactive(self.factory)

    async def policy(self) -> Policy:
        """Kill switch first (fail closed), then paid-spending permission."""
        await self.require_execution_allowed()
        snap = await self.spend.snapshot()
        reason = snap.paid_block_reason(self.clock())
        return Policy(reason is None, reason, snap)

    async def audit_blocked(
        self, user_id: uuid.UUID | None, agent_id: str, kind: str, reason: str
    ) -> None:
        """Durable record that something was refused (survives the caller's rollback)."""
        from core.audit.service import Actor

        actor = Actor.user(user_id) if user_id else SYSTEM
        await record_event_durable(
            self.factory, actor, f"execution.{kind}", "agent", agent_id, {"reason": reason}
        )

    async def begin(self, **kw: Any) -> tuple[uuid.UUID, str]:
        return await executions.begin_execution(self.factory, **kw)

    async def finish(
        self, execution_id: uuid.UUID, status: str, failure_reason: str | None = None
    ) -> None:
        await executions.finish_execution(self.factory, execution_id, status, failure_reason)

    async def call(self, **fields: Any) -> uuid.UUID:
        return await executions.record_call(self.factory, **fields)
