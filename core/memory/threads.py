"""Thread ownership for working memory (LangGraph checkpoints).

Every checkpoint thread is registered in agent_threads with its user and agent. That gives:
  isolation  - an agent can only open its OWN threads; a user only theirs (others look nonexistent);
  retention  - threads expire (default 7 days) and are purged;
  deletion   - deleting a thread removes EVERY historical checkpoint, write and blob for it.
"""

import uuid
from collections.abc import Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.memory.types import MemoryNotFoundError
from core.permissions.errors import PermissionDeniedError
from core.permissions.runtime import RunContext
from database.schemas import AgentThread


class ThreadService:
    def __init__(
        self,
        session: AsyncSession,
        saver: Any,
        *,
        retention_days: int = 7,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._s = session
        self._saver = saver
        self._days = retention_days
        self._clock: Callable[[], datetime] = clock or (lambda: datetime.now(UTC))

    @staticmethod
    def config(thread_id: str) -> dict[str, Any]:
        return {"configurable": {"thread_id": thread_id}}

    async def create(self, ctx: RunContext) -> str:
        now = self._clock()
        thread_id = uuid.uuid4().hex
        self._s.add(
            AgentThread(
                thread_id=thread_id,
                user_id=ctx.user_id,
                agent_id=ctx.agent_id,
                execution_id=ctx.execution_id,
                last_activity_at=now,
                expires_at=now + timedelta(days=self._days),
            )
        )
        await self._s.flush()
        return thread_id

    async def authorize(
        self, thread_id: str, *, user_id: uuid.UUID, agent_id: str | None = None
    ) -> AgentThread:
        row = await self._s.get(AgentThread, thread_id, populate_existing=True)
        if row is None or row.user_id != user_id:
            raise MemoryNotFoundError(
                "thread not found"
            )  # another user's thread is indistinguishable
        if agent_id is not None and row.agent_id != agent_id:
            raise PermissionDeniedError("thread belongs to another agent")
        return row

    async def open(self, ctx: RunContext, thread_id: str) -> dict[str, Any]:
        """The LangGraph config for a thread this run is allowed to use."""
        row = await self.authorize(thread_id, user_id=ctx.user_id, agent_id=ctx.agent_id)
        if row.expires_at <= self._clock():
            raise MemoryNotFoundError("thread expired")
        await self._s.execute(
            update(AgentThread)
            .where(AgentThread.thread_id == thread_id)
            .values(last_activity_at=self._clock())
        )
        return self.config(thread_id)

    async def list_for_user(self, user_id: uuid.UUID) -> list[AgentThread]:
        rows = await self._s.execute(
            select(AgentThread)
            .where(AgentThread.user_id == user_id)
            .order_by(AgentThread.created_at.desc())
        )
        return list(rows.scalars())

    async def delete(self, user_id: uuid.UUID, thread_ids: Sequence[str]) -> int:
        """Delete every checkpoint of the user's threads, then the ownership rows. Checkpoints go
        FIRST: if the second step failed we would rather keep an ownership row than an orphan blob."""
        owned = list(
            (
                await self._s.execute(
                    select(AgentThread.thread_id).where(
                        AgentThread.user_id == user_id, AgentThread.thread_id.in_(list(thread_ids))
                    )
                )
            ).scalars()
        )
        for tid in owned:
            await self._saver.adelete_thread(tid)
        if owned:
            await self._s.execute(delete(AgentThread).where(AgentThread.thread_id.in_(owned)))
            await self._s.flush()
        return len(owned)

    async def purge_expired(self, now: datetime | None = None) -> int:
        now = now or self._clock()
        expired = list(
            (
                await self._s.execute(
                    select(AgentThread.thread_id).where(AgentThread.expires_at <= now)
                )
            ).scalars()
        )
        for tid in expired:
            await self._saver.adelete_thread(tid)
        if expired:
            await self._s.execute(delete(AgentThread).where(AgentThread.thread_id.in_(expired)))
            await self._s.flush()
        return len(expired)
