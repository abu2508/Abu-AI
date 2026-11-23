"""/v1/agents and /v1/audit. Read access, plus disabling an agent.

Disabling is the only mutation exposed to app tokens because it can only REDUCE privilege.
Enabling, activating versions and every permission grant are administrator actions (CLI):
a stolen app token must not be able to widen what an agent can do.
"""

from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from core.agent_registry import service as registry
from core.audit.service import Actor
from core.model_registry.errors import NotFoundError
from database.schemas import Agent, AuditEvent
from services.api.auth.deps import PrincipalDep, SessionDep

router = APIRouter(prefix="/v1", tags=["agents"])


class VersionOut(BaseModel):
    version: int
    state: str
    max_steps: int
    timeout_seconds: int
    max_run_input_tokens: int
    max_run_output_tokens: int
    max_run_cost_inr: Decimal
    model_aliases: list[str]
    tools: dict[str, bool]
    memory: dict[str, str]
    activated_at: datetime | None


class AgentOut(BaseModel):
    agent_id: str
    name: str
    description: str
    enabled: bool
    active_version: int | None


class AgentDetail(AgentOut):
    versions: list[VersionOut]


class AuditOut(BaseModel):
    id: int
    occurred_at: datetime
    actor_type: str
    event_type: str
    subject_type: str
    subject_id: str
    detail: dict[str, Any]


def _version_out(v: Any) -> VersionOut:
    return VersionOut(
        version=v.version,
        state=v.state.value,
        max_steps=v.max_steps,
        timeout_seconds=v.timeout_seconds,
        max_run_input_tokens=v.max_run_input_tokens,
        max_run_output_tokens=v.max_run_output_tokens,
        max_run_cost_inr=v.max_run_cost_inr,
        activated_at=v.activated_at,
        model_aliases=sorted(a.alias for a in v.model_aliases),
        tools={t.tool_name: t.requires_approval for t in v.tools},
        memory={m.namespace: m.mode.value for m in v.memory},
    )


async def _agent_out(session: SessionDep, agent: Agent) -> AgentOut:
    active = await registry.get_active_version(session, agent.agent_id)
    return AgentOut(
        agent_id=agent.agent_id,
        name=agent.name,
        description=agent.description,
        enabled=agent.enabled,
        active_version=active.version if active else None,
    )


@router.get("/agents", response_model=list[AgentOut])
async def list_agents(_: PrincipalDep, session: SessionDep) -> list[AgentOut]:
    agents = (await session.execute(select(Agent).order_by(Agent.agent_id))).scalars().all()
    return [await _agent_out(session, a) for a in agents]


@router.get("/agents/{agent_id}", response_model=AgentDetail)
async def get_agent(agent_id: str, _: PrincipalDep, session: SessionDep) -> AgentDetail:
    try:
        agent = await registry.get_agent(session, agent_id)
    except NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found") from None
    base = await _agent_out(session, agent)
    versions = await registry.list_versions(session, agent_id)
    return AgentDetail(**base.model_dump(), versions=[_version_out(v) for v in versions])


@router.post("/agents/{agent_id}/disable", response_model=AgentOut)
async def disable_agent(agent_id: str, principal: PrincipalDep, session: SessionDep) -> AgentOut:
    try:
        agent = await registry.set_agent_enabled(
            session, agent_id, False, Actor.user(principal.user_id, principal.session_id)
        )
    except NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "agent not found") from None
    out = await _agent_out(session, agent)
    await session.commit()
    return out


@router.get("/audit", response_model=list[AuditOut])
async def audit_log(
    _: PrincipalDep,
    session: SessionDep,
    limit: int = Query(default=50, ge=1, le=200),
    before_id: int | None = Query(default=None, ge=1),
) -> list[AuditOut]:
    stmt = select(AuditEvent).order_by(AuditEvent.id.desc()).limit(limit)
    if before_id is not None:
        stmt = stmt.where(AuditEvent.id < before_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AuditOut(
            id=r.id,
            occurred_at=r.occurred_at,
            actor_type=r.actor_type,
            event_type=r.event_type,
            subject_type=r.subject_type,
            subject_id=r.subject_id,
            detail=r.detail,
        )
        for r in rows
    ]
