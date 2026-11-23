"""/v1/memory - the user's own Memory Manager.

These routes are for the authenticated USER over their own data. They stay available while the kill
switch is active (it stops agents, not the owner's access to or deletion of their data). Embedding
vectors are never returned. Another user's ids look exactly like nonexistent ones (404).
"""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, HTTPException, Query, Request, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select

from core.governance.service import Governance
from core.memory import namespaces as ns_rules
from core.memory.crypto import DecryptionError, KeyUnavailableError
from core.memory.privacy import TextRejectedError
from core.memory.service import MemoryService
from core.memory.structured import StructuredValidationError
from core.memory.types import (
    Caller,
    InvalidStateError,
    MemoryNotFoundError,
    MemoryProposal,
    MemoryType,
    MemoryView,
    PolicyViolationError,
    Sensitivity,
)
from core.permissions.errors import PermissionDeniedError
from database.schemas import Agent, MemoryAccessLog
from services.api.auth.deps import PrincipalDep, SessionDep

router = APIRouter(prefix="/v1/memory", tags=["memory"])


class MemoryOut(BaseModel):
    id: uuid.UUID
    namespace: str
    memory_type: str
    status: str
    key: str | None
    schema_name: str | None
    text: str | None
    data: dict[str, Any] | None
    embed_text: str | None
    sensitivity: str
    source_kind: str
    source_ref: str | None
    created_by: str
    confidence: float
    flags: list[str]
    embedding_state: str
    created_at: datetime
    confirmed_at: datetime | None
    supersedes_id: uuid.UUID | None
    retention_expires_at: datetime | None


class MemoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    namespace: str
    memory_type: MemoryType
    text: str | None = Field(default=None, max_length=4000)
    data: dict[str, Any] | None = None
    schema_name: str | None = None
    key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
    sensitivity: Sensitivity = "personal"
    embed_text: str | None = Field(default=None, max_length=1000)
    allow_embedding: bool = False
    retention_days: int | None = Field(default=None, ge=1, le=3650)


class MemoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str | None = Field(default=None, max_length=4000)
    data: dict[str, Any] | None = None
    embed_text: str | None = Field(default=None, max_length=1000)


class ApproveBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    allow_embedding: bool = False
    embed_text: str | None = Field(default=None, max_length=1000)
    text: str | None = Field(default=None, max_length=4000)


class EraseBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    confirm: Literal["ERASE ALL MY MEMORY"]


class ReceiptOut(BaseModel):
    records: int
    embeddings: int
    threads: int
    access_log_rows_scrubbed: int


class ThreadOut(BaseModel):
    thread_id: str
    agent_id: str
    created_at: datetime
    last_activity_at: datetime
    expires_at: datetime


class AccessOut(BaseModel):
    occurred_at: datetime
    actor_type: str
    actor_id: str
    action: str
    namespace: str | None
    result_count: int
    memory_id: uuid.UUID | None


def _out(v: MemoryView) -> MemoryOut:
    d = {k: getattr(v, k) for k in MemoryOut.model_fields}
    return MemoryOut(**d)


def _service(request: Request, session: SessionDep) -> MemoryService:
    runtime = request.app.state.memory
    factory = request.app.state.sessionmaker
    if runtime is None or factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "memory is not available")
    service: MemoryService = runtime.service(
        session, Governance(factory), thread_days=request.app.state.settings.retention_thread_days
    )
    return service


def _http(exc: Exception) -> HTTPException:
    """Map domain errors to HTTP without ever echoing memory content."""
    if isinstance(exc, MemoryNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    if isinstance(exc, PermissionDeniedError):
        return HTTPException(status.HTTP_403_FORBIDDEN, str(exc))
    if isinstance(exc, InvalidStateError):
        return HTTPException(status.HTTP_409_CONFLICT, str(exc))
    if isinstance(
        exc,
        PolicyViolationError
        | TextRejectedError
        | StructuredValidationError
        | ns_rules.NamespaceError,
    ):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc))
    if isinstance(exc, KeyUnavailableError | DecryptionError):
        return HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "memory is temporarily unavailable"
        )
    raise exc


async def _check_root(session: SessionDep, namespace: str) -> None:
    ns_rules.validate_format(namespace)
    root = ns_rules.owner(namespace)
    if root != ns_rules.USER_ROOT and await session.get(Agent, root) is None:
        raise PolicyViolationError("the namespace root must be 'user' or a registered agent")


@router.get("", response_model=list[MemoryOut])
async def list_memory(
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
    namespace: str | None = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    memory_type: str | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[MemoryOut]:
    svc = _service(request, session)
    try:
        views = await svc.list_memories(
            Caller.user(principal.user_id),
            namespace=namespace,
            status=status_,
            memory_type=memory_type,
            limit=limit,
        )
        await session.commit()
    except Exception as exc:
        raise _http(exc) from None
    return [_out(v) for v in views]


@router.get("/proposals", response_model=list[MemoryOut])
async def proposals(
    principal: PrincipalDep, request: Request, session: SessionDep
) -> list[MemoryOut]:
    return await list_memory(principal, request, session, status_="proposed")


@router.get("/access-log", response_model=list[AccessOut])
async def access_log(
    principal: PrincipalDep, session: SessionDep, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[AccessOut]:
    rows = (
        await session.execute(
            select(MemoryAccessLog)
            .where(MemoryAccessLog.user_id == principal.user_id)
            .order_by(MemoryAccessLog.id.desc())
            .limit(limit)
        )
    ).scalars()
    return [
        AccessOut(
            occurred_at=r.occurred_at,
            actor_type=r.actor_type,
            actor_id=r.actor_id,
            action=r.action,
            namespace=r.namespace,
            result_count=r.result_count,
            memory_id=r.memory_id,
        )
        for r in rows
    ]


@router.get("/threads", response_model=list[ThreadOut])
async def list_threads(
    principal: PrincipalDep, request: Request, session: SessionDep
) -> list[ThreadOut]:
    runtime = request.app.state.memory
    threads = runtime.threads(session) if runtime else None
    if threads is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "working memory is not available")
    return [
        ThreadOut(
            thread_id=t.thread_id,
            agent_id=t.agent_id,
            created_at=t.created_at,
            last_activity_at=t.last_activity_at,
            expires_at=t.expires_at,
        )
        for t in await threads.list_for_user(principal.user_id)
    ]


@router.delete("/threads/{thread_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_thread(
    thread_id: str, principal: PrincipalDep, request: Request, session: SessionDep
) -> Response:
    runtime = request.app.state.memory
    threads = runtime.threads(session) if runtime else None
    if threads is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "working memory is not available")
    if await threads.delete(principal.user_id, [thread_id]) == 0:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "not found")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/erase", response_model=ReceiptOut)
async def erase_everything(
    body: EraseBody, principal: PrincipalDep, request: Request, session: SessionDep
) -> ReceiptOut:
    """Right to erasure: all memory, all threads/checkpoints and all memory access logs of the caller."""
    svc = _service(request, session)
    receipt = await svc.erase_user(Caller.user(principal.user_id))
    await session.commit()
    return ReceiptOut(
        records=receipt.records,
        embeddings=receipt.embeddings,
        threads=receipt.threads,
        access_log_rows_scrubbed=receipt.access_log_rows_scrubbed,
    )


@router.post("", response_model=MemoryOut, status_code=status.HTTP_201_CREATED)
async def create_memory(
    body: MemoryCreate, principal: PrincipalDep, request: Request, session: SessionDep
) -> MemoryOut:
    svc = _service(request, session)
    caller = Caller.user(principal.user_id)
    try:
        await _check_root(session, body.namespace)
        proposal = MemoryProposal(
            namespace=body.namespace,
            memory_type=body.memory_type,
            text=body.text,
            data=body.data,
            schema_name=body.schema_name,
            key=body.key,
            sensitivity=body.sensitivity,
            embed_text=body.embed_text,
            source_kind="user_created",
            confidence=1.0,
            retention_days=body.retention_days,
        )
        result = await svc.propose(caller, proposal)
        if body.allow_embedding:
            view = await svc.approve_embedding(caller, result.id, body.embed_text)
        else:
            view = await svc.get(caller, result.id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return _out(view)


@router.get("/{memory_id}", response_model=MemoryOut)
async def get_memory(
    memory_id: uuid.UUID, principal: PrincipalDep, request: Request, session: SessionDep
) -> MemoryOut:
    svc = _service(request, session)
    try:
        view = await svc.get(Caller.user(principal.user_id), memory_id)
        await session.commit()
    except Exception as exc:
        raise _http(exc) from None
    return _out(view)


@router.put("/{memory_id}", response_model=MemoryOut)
async def update_memory(
    memory_id: uuid.UUID,
    body: MemoryUpdate,
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
) -> MemoryOut:
    svc = _service(request, session)
    caller = Caller.user(principal.user_id)
    try:
        current = await svc.get(caller, memory_id)
        proposal = MemoryProposal(
            namespace=current.namespace,
            memory_type=current.memory_type,
            text=body.text if body.text is not None else current.text,
            data=body.data if body.data is not None else current.data,
            schema_name=current.schema_name,
            key=current.key,
            sensitivity=current.sensitivity,
            embed_text=body.embed_text,
            source_kind="user_created",
            confidence=1.0,
        )
        result = await svc.update(caller, memory_id, proposal)
        view = await svc.get(caller, result.id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return _out(view)


@router.post("/{memory_id}/approve", response_model=MemoryOut)
async def approve_memory(
    memory_id: uuid.UUID,
    body: ApproveBody,
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
) -> MemoryOut:
    svc = _service(request, session)
    try:
        view = await svc.approve(
            Caller.user(principal.user_id),
            memory_id,
            allow_embedding=body.allow_embedding,
            embed_text=body.embed_text,
            text_edit=body.text,
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return _out(view)


@router.post("/{memory_id}/reject", status_code=status.HTTP_204_NO_CONTENT)
async def reject_memory(
    memory_id: uuid.UUID, principal: PrincipalDep, request: Request, session: SessionDep
) -> Response:
    svc = _service(request, session)
    try:
        await svc.reject(Caller.user(principal.user_id), memory_id)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.delete("/{memory_id}", response_model=ReceiptOut)
async def delete_memory(
    memory_id: uuid.UUID,
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
    purge_sources: bool = True,
) -> ReceiptOut:
    """Deletes the record, derived summaries, older versions, embeddings and (by default) every
    checkpoint of the conversation thread it came from."""
    svc = _service(request, session)
    try:
        receipt = await svc.delete_record(
            Caller.user(principal.user_id), memory_id, purge_sources=purge_sources
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return ReceiptOut(
        records=receipt.records,
        embeddings=receipt.embeddings,
        threads=receipt.threads,
        access_log_rows_scrubbed=receipt.access_log_rows_scrubbed,
    )


@router.delete("", response_model=ReceiptOut)
async def delete_namespace(
    namespace: str,
    principal: PrincipalDep,
    request: Request,
    session: SessionDep,
    purge_sources: bool = True,
) -> ReceiptOut:
    svc = _service(request, session)
    try:
        receipt = await svc.delete_namespace(
            Caller.user(principal.user_id), namespace, purge_sources=purge_sources
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        raise _http(exc) from None
    return ReceiptOut(
        records=receipt.records,
        embeddings=receipt.embeddings,
        threads=receipt.threads,
        access_log_rows_scrubbed=receipt.access_log_rows_scrubbed,
    )
