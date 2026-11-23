"""/v1/governance and /v1/executions.

What an authenticated app token may do here:
  READ     status (kill switch + spending usage), its own execution history
  ACTIVATE the global kill switch                (reduces what the system can do)
Everything that would RAISE capability or spend is operator-only (admin CLI): deactivating the kill
switch, re-enabling agents, budgets, rates, model policies, provider configuration. Those have no
working API; the explicit routes below exist ONLY to answer 403 and write a durable audit record of
the attempt, so a compromised token probing for privilege leaves a trail.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from core.audit.service import Actor
from core.governance import killswitch
from core.governance.errors import GovernanceUnavailableError
from core.governance.service import Governance
from database.schemas import Execution, ExecutionCall
from services.api.auth.deps import PrincipalDep, SessionDep
from services.api.auth.service import Principal

router = APIRouter(prefix="/v1", tags=["governance"])


class KillSwitchOut(BaseModel):
    active: bool
    reason: str | None
    activated_at: datetime | None
    activated_by: str | None  # "operator" | "app"
    activated_via: str | None  # cli | api


class SpendingOut(BaseModel):
    monthly_budget_inr: Decimal
    daily_budget_inr: Decimal
    paid_models_enabled: bool
    paid_frozen: bool
    paid_frozen_reason: str | None
    usd_inr_rate: Decimal | None
    rate_source: str | None
    day_used_inr: Decimal
    month_used_inr: Decimal
    month_remaining_inr: Decimal
    held_inr: Decimal


class StatusOut(BaseModel):
    kill_switch: KillSwitchOut
    spending: SpendingOut


class ActivateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=500)


def _gov(request: Request) -> Governance:
    factory = request.app.state.sessionmaker
    if factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service unavailable")
    return Governance(factory)


def _ks_out(snap: killswitch.KillSwitchSnapshot) -> KillSwitchOut:
    by = {"admin": "operator", "user": "app"}.get(snap.activated_by_type or "")
    return KillSwitchOut(
        active=snap.active,
        reason=snap.reason,
        activated_at=snap.activated_at,
        activated_by=by,
        activated_via=snap.activated_via,
    )


@router.get("/governance/status", response_model=StatusOut)
async def governance_status(_: PrincipalDep, request: Request) -> StatusOut:
    gov = _gov(request)
    try:
        ks = await killswitch.read_state(gov.factory)
        cfg = await gov.spend.snapshot()
        usage = await gov.spend.usage()
    except GovernanceUnavailableError:
        # Execution is blocked (fail closed) in this state; say so rather than guess.
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "governance state unavailable; execution is blocked",
        ) from None
    return StatusOut(
        kill_switch=_ks_out(ks),
        spending=SpendingOut(
            monthly_budget_inr=cfg.monthly_budget_inr,
            daily_budget_inr=cfg.daily_budget_inr,
            paid_models_enabled=cfg.paid_models_enabled,
            paid_frozen=cfg.paid_frozen,
            paid_frozen_reason=cfg.paid_frozen_reason,
            usd_inr_rate=cfg.usd_inr_rate,
            rate_source=cfg.rate_source,
            day_used_inr=usage.day_used_inr,
            month_used_inr=usage.month_used_inr,
            month_remaining_inr=max(Decimal(0), cfg.monthly_budget_inr - usage.month_used_inr),
            held_inr=usage.held_inr,
        ),
    )


@router.post("/governance/kill-switch", response_model=KillSwitchOut)
async def activate_kill_switch(
    body: ActivateRequest, principal: PrincipalDep, request: Request
) -> KillSwitchOut:
    """Stop all NEW model calls, tool calls and agent executions. Idempotent. Operator-only to undo."""
    gov = _gov(request)
    try:
        snap = await killswitch.activate(
            gov.factory, Actor.user(principal.user_id, principal.session_id), body.reason
        )
    except ValueError:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "a reason is required") from None
    except GovernanceUnavailableError:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "governance state unavailable"
        ) from None
    return _ks_out(snap)


async def _deny(request: Request, principal: Principal, action: str) -> None:
    await killswitch.deny_privileged_action(
        _gov(request).factory, Actor.user(principal.user_id, principal.session_id), action
    )
    raise HTTPException(status.HTTP_403_FORBIDDEN, "this action is operator-only")


@router.delete("/governance/kill-switch", status_code=403)
async def deny_deactivate(principal: PrincipalDep, request: Request) -> None:
    await _deny(request, principal, "kill_switch.deactivate")


@router.put("/governance/spending", status_code=403)
async def deny_spending(principal: PrincipalDep, request: Request) -> None:
    await _deny(request, principal, "spending.modify")


@router.put("/governance/models", status_code=403)
async def deny_models(principal: PrincipalDep, request: Request) -> None:
    await _deny(request, principal, "model_policy.modify")


@router.put("/governance/providers", status_code=403)
async def deny_providers(principal: PrincipalDep, request: Request) -> None:
    await _deny(request, principal, "provider_config.modify")


@router.post("/agents/{agent_id}/enable", status_code=403)
async def deny_enable(agent_id: str, principal: PrincipalDep, request: Request) -> None:
    await _deny(request, principal, f"agent.enable:{agent_id[:64]}")


# ------------------------------------------------------------------ execution history (redacted)


class CallOut(BaseModel):
    step: int
    attempt: int
    status: str
    failure_reason: str | None
    model_alias: str
    model_name: str | None
    provider: str | None
    provider_model_id: str | None
    input_tokens: int
    output_tokens: int
    estimated_cost_inr: Decimal
    confirmed_cost_inr: Decimal | None
    cost_status: str
    currency: str
    usd_inr_rate: Decimal | None
    latency_ms: float | None
    fallback_used: bool
    fallback_from: str | None
    policy_decisions: dict[str, object]
    started_at: datetime
    completed_at: datetime | None


class ExecutionOut(BaseModel):
    id: uuid.UUID
    trace_id: str
    agent_id: str
    agent_version: int
    task_type: str
    status: str
    failure_reason: str | None
    started_at: datetime
    completed_at: datetime | None
    call_count: int
    total_input_tokens: int
    total_output_tokens: int
    total_estimated_cost_inr: Decimal
    total_confirmed_cost_inr: Decimal


class ExecutionDetail(ExecutionOut):
    calls: list[CallOut]


@router.get("/executions", response_model=list[ExecutionOut])
async def list_executions(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=100)] = 25
) -> list[ExecutionOut]:
    rows = (
        await session.execute(
            select(Execution)
            .where(Execution.user_id == principal.user_id)  # ownership boundary
            .order_by(Execution.started_at.desc())
            .limit(limit)
        )
    ).scalars()
    return [ExecutionOut.model_validate(r, from_attributes=True) for r in rows]


@router.get("/executions/{execution_id}", response_model=ExecutionDetail)
async def get_execution(
    execution_id: uuid.UUID, principal: PrincipalDep, session: SessionDep
) -> ExecutionDetail:
    row = await session.get(Execution, execution_id)
    if row is None or row.user_id != principal.user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "execution not found")
    calls = (
        await session.execute(
            select(ExecutionCall)
            .where(ExecutionCall.execution_id == execution_id)
            .order_by(ExecutionCall.step, ExecutionCall.attempt)
        )
    ).scalars()
    base = ExecutionOut.model_validate(row, from_attributes=True)
    return ExecutionDetail(
        **base.model_dump(), calls=[CallOut.model_validate(c, from_attributes=True) for c in calls]
    )
