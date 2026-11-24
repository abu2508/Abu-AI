"""/v1/messaging - the user's own imported conversations. Import and sharing are explicit user actions.
There is NO route that sends, forwards or posts a message, and none can be added without a new milestone."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from domain.messaging.service import MessagingError, MessagingService
from services.api.auth.deps import PrincipalDep, SessionDep
from services.api.domain_deps import CipherDep, jsonable

router = APIRouter(prefix="/v1/messaging", tags=["messaging"])
Platform = Literal["whatsapp", "instagram", "telegram", "sms", "imessage", "other"]
Tone = Literal["casual", "warm", "formal", "brief"]
Relationship = Literal["friend", "family", "partner", "colleague", "acquaintance", "other"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContactIn(Body):
    name: str = Field(min_length=1, max_length=80)
    relationship: Relationship = "other"
    notes: str | None = Field(default=None, max_length=1000)


class ContactPatch(Body):
    name: str | None = Field(default=None, min_length=1, max_length=80)
    relationship: Relationship | None = None
    notes: str | None = Field(default=None, max_length=1000)


class ImportIn(Body):
    platform: Platform
    format: Literal["whatsapp_export", "labelled", "json"]
    text: str = Field(min_length=1, max_length=900_000)
    my_name: str | None = Field(default=None, max_length=60)
    contact_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=80)
    share_with_assistant: bool = True
    retention_days: int = Field(default=30, ge=1, le=365)
    tone_pref: Tone = "casual"


class AppendIn(Body):
    format: Literal["whatsapp_export", "labelled", "json"]
    text: str = Field(min_length=1, max_length=900_000)
    my_name: str | None = Field(default=None, max_length=60)


class InboxItem(BaseModel):
    id: uuid.UUID
    slug: str
    platform: str
    title: str | None
    contact_id: uuid.UUID | None
    shared_with_assistant: bool
    message_count: int
    last_message_at: datetime | None
    last_sender: Literal["me", "them"] | None
    needs_reply: bool
    waiting_hours: float | None
    timestamps_reliable: bool
    # Derived from message CONTENT: null unless the conversation is explicitly shared with Abu AI.
    last_message_intent: str | None
    flagged_messages: int | None


class ConversationPatch(Body):
    shared_with_assistant: bool | None = None
    tone_pref: Tone | None = None
    retention_days: int | None = Field(default=None, ge=1, le=365)
    contact_id: uuid.UUID | None = None
    title: str | None = Field(default=None, max_length=80)


async def _run(session: SessionDep, fn: Any) -> Any:
    try:
        res = await fn()
        await session.commit()
        return jsonable(res)
    except MessagingError as exc:
        await session.rollback()
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
        )


@router.post("/contacts", status_code=201)
async def create_contact(
    body: ContactIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return s.contact_view(await s.create_contact(principal.user_id, **body.model_dump()))

    return await _run(session, go)


@router.get("/contacts")
async def list_contacts(principal: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = MessagingService(session, cipher)
    return jsonable([s.contact_view(c) for c in await s.contacts(principal.user_id)])


@router.patch("/contacts/{contact_id}")
async def patch_contact(
    contact_id: uuid.UUID,
    body: ContactPatch,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return s.contact_view(
            await s.update_contact(
                principal.user_id, contact_id, **body.model_dump(exclude_unset=True)
            )
        )

    return await _run(session, go)


@router.delete(
    "/contacts/{contact_id}", status_code=204, response_class=Response, response_model=None
)
async def delete_contact(
    contact_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> None:
        await s.delete_contact(principal.user_id, contact_id)

    res = await _run(session, go)
    return res if isinstance(res, JSONResponse) else Response(status_code=204)


@router.post("/conversations/import", status_code=201)
async def import_conversation(
    body: ImportIn, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return await s.import_conversation(
            principal.user_id,
            platform=body.platform,
            fmt=body.format,
            text=body.text,
            my_name=body.my_name,
            contact_id=body.contact_id,
            title=body.title,
            share_with_assistant=body.share_with_assistant,
            retention_days=body.retention_days,
            tone_pref=body.tone_pref,
        )

    return await _run(session, go)


@router.get("/conversations")
async def list_conversations(
    principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)
    return jsonable([s.conversation_view(c) for c in await s.conversations(principal.user_id)])


@router.get("/inbox", response_model=list[InboxItem])
async def inbox(
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    shared_only: bool = False,
) -> Any:
    """The owner's inbox in one paged request (constant query count). Content-derived fields are null for
    conversations not shared with Abu AI; no message text is ever returned."""
    s = MessagingService(session, cipher)
    items, total = await s.inbox(
        principal.user_id, limit=limit, offset=offset, shared_only=shared_only
    )
    response.headers["X-Total-Count"] = str(total)
    return jsonable(items)


@router.get("/conversations/{conv_id}")
async def get_conversation(
    conv_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return s.conversation_view(await s.conversation(principal.user_id, conv_id))

    return await _run(session, go)


@router.patch("/conversations/{conv_id}")
async def patch_conversation(
    conv_id: uuid.UUID,
    body: ConversationPatch,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return s.conversation_view(
            await s.update(principal.user_id, conv_id, **body.model_dump(exclude_unset=True))
        )

    return await _run(session, go)


@router.post("/conversations/{conv_id}/messages")
async def append_messages(
    conv_id: uuid.UUID,
    body: AppendIn,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return await s.append(
            principal.user_id, conv_id, fmt=body.format, text=body.text, my_name=body.my_name
        )

    return await _run(session, go)


@router.get("/conversations/{conv_id}/messages")
async def list_messages(
    conv_id: uuid.UUID,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return await s.messages(principal.user_id, conv_id, limit, offset)

    return await _run(session, go)


@router.get("/conversations/{conv_id}/analysis")
async def analysis(
    conv_id: uuid.UUID, principal: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return await s.analysis(principal.user_id, conv_id)

    return await _run(session, go)


@router.get("/conversations/{conv_id}/suggestions")
async def suggestions(
    conv_id: uuid.UUID,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    tone: Tone | None = None,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> Any:
        return await s.suggestions(principal.user_id, conv_id, tone=tone)

    return await _run(session, go)


@router.delete(
    "/conversations/{conv_id}", status_code=204, response_class=Response, response_model=None
)
async def delete_conversation(
    conv_id: uuid.UUID,
    principal: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    request: Request,
) -> Any:
    s = MessagingService(session, cipher)

    async def go() -> None:
        await s.delete_conversation(principal.user_id, conv_id)

    res = await _run(session, go)
    return res if isinstance(res, JSONResponse) else Response(status_code=204)
