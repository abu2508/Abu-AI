"""/v1/orchestrator - authenticated entry point to the governed orchestrator.

* The execution identity is the AUTHENTICATED user (token -> Principal). The body has no user field and
  extra fields are rejected, so a caller cannot ask to run as someone else.
* Responses never contain prompts, secrets, memory text or the request text; only the model-validated
  reply, counts and ids. History endpoints return metadata only and only the caller's own runs.
* While the kill switch is active, every run endpoint that would execute returns 503.
"""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from core.orchestrator.orchestrator import MAX_MESSAGE_CHARS, Orchestrator
from core.orchestrator.types import OrchestratorError, OrchestratorResponse, RunFailed
from services.api.auth.deps import PrincipalDep

router = APIRouter(prefix="/v1/orchestrator", tags=["orchestrator"])


class RunRequest(BaseModel):
    model_config = ConfigDict(
        extra="forbid"
    )  # no user_id / agent_id / model / workflow fields exist
    message: str = Field(min_length=1, max_length=MAX_MESSAGE_CHARS)


class RunSummary(BaseModel):
    run_id: uuid.UUID
    status: str
    outcome: str | None
    intent: str | None
    agent_id: str | None
    workflow: str | None
    error_code: str | None
    execution_id: uuid.UUID | None
    origin: str
    created_at: datetime
    completed_at: datetime | None
    response_available: bool


class RunDetail(RunSummary):
    response: OrchestratorResponse | None = None
    reply_expired: bool = False


class WorkflowInfo(BaseModel):
    name: str
    intent: str
    agent_id: str
    description: str
    mock: bool
    max_graph_steps: int
    timeout_seconds: int
    max_retries: int
    tool_allowlist: list[str]


def _orch(request: Request) -> Orchestrator:
    o: Orchestrator | None = getattr(request.app.state, "orchestrator", None)
    if o is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "orchestrator is not available")
    return o


def _summary(r: Any) -> dict[str, Any]:
    return {
        "run_id": r.id,
        "status": r.status,
        "outcome": r.outcome,
        "intent": r.intent,
        "agent_id": r.agent_id,
        "workflow": r.workflow,
        "error_code": r.error_code,
        "execution_id": r.execution_id,
        "origin": r.origin,
        "created_at": r.created_at,
        "completed_at": r.completed_at,
        "response_available": r.response_enc is not None,
    }


@router.post("/run", response_model=OrchestratorResponse)
async def run(
    body: RunRequest,
    principal: PrincipalDep,
    request: Request,
    idempotency_key: Annotated[
        str | None, Header(min_length=16, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")
    ] = None,
) -> OrchestratorResponse | JSONResponse:
    orch = _orch(request)
    try:
        return await orch.run_user_request(
            principal.user_id, body.message, idempotency_key=idempotency_key
        )
    except RunFailed as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={
                "error": {"code": exc.code, "message": exc.message},
                "run_id": str(exc.run_id),
                "execution_id": str(exc.execution_id) if exc.execution_id else None,
                "trace_id": exc.trace_id,
            },
        )
    except OrchestratorError as exc:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": {"code": exc.code, "message": exc.message}},
        )


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    principal: PrincipalDep,
    request: Request,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    before: datetime | None = None,
) -> list[RunSummary]:
    rows = await _orch(request).store.list(principal.user_id, limit, before)
    return [RunSummary(**_summary(r)) for r in rows]


@router.get("/runs/{run_id}", response_model=RunDetail)
async def get_run(run_id: uuid.UUID, principal: PrincipalDep, request: Request) -> RunDetail:
    orch = _orch(request)
    row = await orch.store.get(principal.user_id, run_id)
    if row is None:  # unknown id and someone else's id are indistinguishable
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    response = orch.store.open_response(row)
    return RunDetail(
        **_summary(row),
        response=response,
        reply_expired=row.response_enc is not None and response is None,
    )


@router.get("/workflows", response_model=list[WorkflowInfo])
async def workflows(principal: PrincipalDep, request: Request) -> list[WorkflowInfo]:
    del principal
    return [
        WorkflowInfo(
            name=s.name,
            intent=s.intent.value,
            agent_id=s.agent_id,
            description=s.description,
            mock=s.mock,
            max_graph_steps=s.max_graph_steps,
            timeout_seconds=s.timeout_seconds,
            max_retries=s.max_retries,
            tool_allowlist=list(s.tool_allowlist),
        )
        for s in _orch(request).registry.all()
    ]
