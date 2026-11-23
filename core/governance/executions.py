"""Execution records. Metadata only: NEVER prompts, completions, financial records or tool
arguments. Written in their own transactions so they survive a rolled-back request."""

import re
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.schemas import Execution, ExecutionCall

Factory = async_sessionmaker[AsyncSession]

TASK_TYPE = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")
REASON = re.compile(r"^[a-z0-9_.:-]{1,64}$")
FINAL_STATUSES = {"succeeded", "failed", "blocked", "limit_exceeded", "timeout"}


def safe_reason(reason: str | None) -> str | None:
    """Failure reasons are short machine codes, never free text (which could carry content)."""
    if reason is None:
        return None
    return reason if REASON.match(reason) else "unspecified"


async def begin_execution(
    factory: Factory,
    *,
    user_id: uuid.UUID,
    agent_id: str,
    version_id: uuid.UUID,
    version: int,
    task_type: str,
    trace_id: str | None,
) -> tuple[uuid.UUID, str]:
    if not TASK_TYPE.match(task_type):
        raise ValueError("task_type must be a lowercase slug")
    trace = trace_id or uuid.uuid4().hex
    if not re.match(r"^[A-Za-z0-9_.-]{8,64}$", trace):
        raise ValueError("invalid trace id")
    async with factory() as s, s.begin():
        row = Execution(
            trace_id=trace,
            user_id=user_id,
            agent_id=agent_id,
            agent_version_id=version_id,
            agent_version=version,
            task_type=task_type,
            status="running",
        )
        s.add(row)
        await s.flush()
        return row.id, trace


async def finish_execution(
    factory: Factory, execution_id: uuid.UUID, status: str, failure_reason: str | None = None
) -> None:
    if status not in FINAL_STATUSES:
        raise ValueError(f"invalid final status {status!r}")
    async with factory() as s, s.begin():
        totals = (
            await s.execute(
                select(
                    func.count(ExecutionCall.id),
                    func.coalesce(func.sum(ExecutionCall.input_tokens), 0),
                    func.coalesce(func.sum(ExecutionCall.output_tokens), 0),
                    func.coalesce(func.sum(ExecutionCall.estimated_cost_inr), 0),
                    func.coalesce(func.sum(ExecutionCall.confirmed_cost_inr), 0),
                ).where(
                    ExecutionCall.execution_id == execution_id, ExecutionCall.status == "succeeded"
                )
            )
        ).one()
        await s.execute(
            update(Execution)
            .where(Execution.id == execution_id, Execution.status == "running")  # first finish wins
            .values(
                status=status,
                failure_reason=safe_reason(failure_reason),
                completed_at=datetime.now(UTC),
                call_count=totals[0],
                total_input_tokens=totals[1],
                total_output_tokens=totals[2],
                total_estimated_cost_inr=Decimal(str(totals[3])),
                total_confirmed_cost_inr=Decimal(str(totals[4])),
            )
        )


async def record_call(factory: Factory, **fields: Any) -> uuid.UUID:
    fields["failure_reason"] = safe_reason(fields.get("failure_reason"))
    async with factory() as s, s.begin():
        row = ExecutionCall(**fields)
        s.add(row)
        await s.flush()
        return row.id
