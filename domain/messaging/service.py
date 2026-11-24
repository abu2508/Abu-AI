"""Messaging service. Conversations exist only if the user imported/shared them; agents may read only
conversations the user flagged `shared_with_assistant`. There is no operation that sends a message."""

import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import distinct_on, insert
from sqlalchemy.ext.asyncio import AsyncSession

from core.memory.privacy import injection_flags
from database.schemas import MsgContact, MsgConversation, MsgMessage
from domain.common.crypto import FieldCipher
from domain.messaging import analysis as an
from domain.messaging import importers

MAX_CONVERSATIONS = 200
MAX_STORED_MESSAGES_PER_CONVERSATION = 20_000


class MessagingError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class MessagingService:
    def __init__(
        self, session: AsyncSession, cipher: FieldCipher, now: datetime | None = None
    ) -> None:
        self._s, self._c, self._now = session, cipher, now

    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    # ----------------------------------------------------------------- contacts
    async def create_contact(
        self, user_id: uuid.UUID, name: str, relationship: str = "other", notes: str | None = None
    ) -> MsgContact:
        rid = uuid.uuid4()
        row = MsgContact(
            id=rid,
            user_id=user_id,
            name_enc=self._c.seal(user_id, rid, "msg.contact.name", name[:80]),
            relationship=relationship,
            notes_enc=self._c.seal(user_id, rid, "msg.contact.notes", notes[:1000])
            if notes
            else None,
        )
        self._s.add(row)
        await self._s.flush()
        return row

    async def contact(self, user_id: uuid.UUID, contact_id: uuid.UUID) -> MsgContact:
        row = (
            await self._s.execute(
                select(MsgContact).where(MsgContact.id == contact_id, MsgContact.user_id == user_id)
            )
        ).scalar_one_or_none()
        if row is None:
            raise MessagingError("not_found", "contact not found", 404)
        return row

    async def contacts(self, user_id: uuid.UUID) -> list[MsgContact]:
        return list(
            (
                await self._s.execute(
                    select(MsgContact)
                    .where(MsgContact.user_id == user_id)
                    .order_by(MsgContact.created_at)
                )
            ).scalars()
        )

    def contact_view(self, c: MsgContact) -> dict[str, Any]:
        return {
            "id": c.id,
            "name": self._c.open(c.user_id, c.id, "msg.contact.name", c.name_enc),
            "relationship": c.relationship,
            "notes": self._c.open(c.user_id, c.id, "msg.contact.notes", c.notes_enc)
            if c.notes_enc
            else None,
        }

    async def update_contact(
        self,
        user_id: uuid.UUID,
        contact_id: uuid.UUID,
        *,
        name: str | None = None,
        relationship: str | None = None,
        notes: str | None = None,
    ) -> MsgContact:
        c = await self.contact(user_id, contact_id)
        if name:
            c.name_enc = self._c.seal(user_id, c.id, "msg.contact.name", name[:80])
        if relationship:
            c.relationship = relationship
        if notes is not None:
            c.notes_enc = (
                self._c.seal(user_id, c.id, "msg.contact.notes", notes[:1000]) if notes else None
            )
        return c

    async def delete_contact(self, user_id: uuid.UUID, contact_id: uuid.UUID) -> None:
        await self.contact(user_id, contact_id)
        await self._s.execute(
            delete(MsgContact).where(MsgContact.id == contact_id, MsgContact.user_id == user_id)
        )

    # ----------------------------------------------------------------- conversations
    async def conversation(self, user_id: uuid.UUID, conv_id: uuid.UUID) -> MsgConversation:
        row = (
            await self._s.execute(
                select(MsgConversation).where(
                    MsgConversation.id == conv_id, MsgConversation.user_id == user_id
                )
            )
        ).scalar_one_or_none()
        if row is None:
            raise MessagingError("not_found", "conversation not found", 404)
        return row

    async def conversations(
        self, user_id: uuid.UUID, *, shared_only: bool = False
    ) -> list[MsgConversation]:
        q = (
            select(MsgConversation)
            .where(MsgConversation.user_id == user_id)
            .order_by(MsgConversation.last_message_at.desc().nulls_last())
        )
        if shared_only:
            q = q.where(MsgConversation.shared_with_assistant.is_(True))
        return list((await self._s.execute(q)).scalars())

    def conversation_view(self, c: MsgConversation) -> dict[str, Any]:
        return {
            "id": c.id,
            "slug": c.slug,
            "platform": c.platform,
            "contact_id": c.contact_id,
            "title": self._c.open(c.user_id, c.id, "msg.conv.title", c.title_enc)
            if c.title_enc
            else None,
            "shared_with_assistant": c.shared_with_assistant,
            "tone_pref": c.tone_pref,
            "retention_days": c.retention_days,
            "message_count": c.message_count,
            "last_message_at": c.last_message_at,
            "memory_namespace": f"messaging_agent/{c.platform}/{c.slug}",
        }

    async def _insert(
        self, conv: MsgConversation, msgs: list[importers.ParsedMessage]
    ) -> tuple[int, int]:
        user_id = conv.user_id
        existing = (
            await self._s.execute(
                select(func.count())
                .select_from(MsgMessage)
                .where(MsgMessage.conversation_id == conv.id)
            )
        ).scalar_one()
        if existing + len(msgs) > MAX_STORED_MESSAGES_PER_CONVERSATION:
            raise MessagingError("conversation_full", "this conversation holds too many messages")
        expires = self.now() + timedelta(days=conv.retention_days)
        added = dup = 0
        for m in msgs:
            ch = self._c.hash(
                user_id, "msg.content", str(conv.id), m.sender, m.sent_at.isoformat(), m.text
            )
            mid = uuid.uuid4()
            res = await self._s.execute(
                insert(MsgMessage)
                .values(
                    id=mid,
                    user_id=user_id,
                    conversation_id=conv.id,
                    sender=m.sender,
                    sent_at=m.sent_at,
                    body_enc=self._c.seal(user_id, mid, "msg.body", m.text),
                    content_hash=ch,
                    flagged=bool(injection_flags(m.text)),
                    expires_at=expires,
                )
                .on_conflict_do_nothing()
                .returning(MsgMessage.id)
            )
            if res.scalar_one_or_none() is None:
                dup += 1
            else:
                added += 1
        count, last = (
            await self._s.execute(
                select(func.count(), func.max(MsgMessage.sent_at)).where(
                    MsgMessage.conversation_id == conv.id
                )
            )
        ).one()
        conv.message_count, conv.last_message_at, conv.updated_at = count, last, self.now()
        return added, dup

    async def import_conversation(
        self,
        user_id: uuid.UUID,
        *,
        platform: str,
        fmt: str,
        text: str,
        my_name: str | None = None,
        contact_id: uuid.UUID | None = None,
        title: str | None = None,
        share_with_assistant: bool = True,
        retention_days: int = 30,
        tone_pref: str = "casual",
    ) -> dict[str, Any]:
        if (
            await self._s.execute(
                select(func.count())
                .select_from(MsgConversation)
                .where(MsgConversation.user_id == user_id)
            )
        ).scalar_one() >= MAX_CONVERSATIONS:
            raise MessagingError(
                "too_many_conversations", "conversation limit reached; delete some first"
            )
        if contact_id:
            await self.contact(user_id, contact_id)
        try:
            msgs = importers.parse(fmt, text, my_name=my_name, now=self.now())
        except importers.ImportError_ as exc:
            raise MessagingError(
                "import_failed", f"could not read the conversation ({exc})"
            ) from None
        cid = uuid.uuid4()
        conv = MsgConversation(
            id=cid,
            user_id=user_id,
            slug="c-" + secrets.token_hex(4),
            platform=platform,
            contact_id=contact_id,
            title_enc=self._c.seal(user_id, cid, "msg.conv.title", title[:80]) if title else None,
            shared_with_assistant=share_with_assistant,
            tone_pref=tone_pref,
            retention_days=retention_days,
            timestamps_reliable=fmt != "labelled",  # a labelled paste has no real timestamps
        )
        self._s.add(conv)
        await self._s.flush()
        added, dup = await self._insert(conv, msgs)
        return {**self.conversation_view(conv), "imported": added, "duplicates": dup}

    async def append(
        self,
        user_id: uuid.UUID,
        conv_id: uuid.UUID,
        *,
        fmt: str,
        text: str,
        my_name: str | None = None,
    ) -> dict[str, Any]:
        conv = await self.conversation(user_id, conv_id)
        try:
            msgs = importers.parse(fmt, text, my_name=my_name, now=self.now())
        except importers.ImportError_ as exc:
            raise MessagingError(
                "import_failed", f"could not read the conversation ({exc})"
            ) from None
        if fmt == "labelled":
            conv.timestamps_reliable = (
                False  # synthetic timestamps now present: stop reporting times
            )
        added, dup = await self._insert(conv, msgs)
        return {"imported": added, "duplicates": dup, "message_count": conv.message_count}

    async def update(
        self, user_id: uuid.UUID, conv_id: uuid.UUID, **fields: Any
    ) -> MsgConversation:
        conv = await self.conversation(user_id, conv_id)
        if fields.get("contact_id"):
            await self.contact(user_id, fields["contact_id"])
        for k in ("shared_with_assistant", "tone_pref", "retention_days", "contact_id"):
            if k in fields and fields[k] is not None:
                setattr(conv, k, fields[k])
        if fields.get("title") is not None:
            conv.title_enc = self._c.seal(user_id, conv.id, "msg.conv.title", fields["title"][:80])
        if fields.get("retention_days"):  # shortening applies to what is already stored too
            new = self.now() + timedelta(days=fields["retention_days"])
            await self._s.execute(
                update(MsgMessage)
                .where(MsgMessage.conversation_id == conv.id, MsgMessage.expires_at > new)
                .values(expires_at=new)
            )
        return conv

    async def delete_conversation(self, user_id: uuid.UUID, conv_id: uuid.UUID) -> int:
        conv = await self.conversation(user_id, conv_id)
        n = conv.message_count
        await self._s.execute(
            delete(MsgConversation).where(
                MsgConversation.id == conv_id, MsgConversation.user_id == user_id
            )
        )
        return n

    async def messages(
        self, user_id: uuid.UUID, conv_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> list[dict[str, Any]]:
        await self.conversation(user_id, conv_id)
        rows = (
            await self._s.execute(
                select(MsgMessage)
                .where(MsgMessage.conversation_id == conv_id, MsgMessage.user_id == user_id)
                .order_by(MsgMessage.sent_at.desc())
                .limit(limit)
                .offset(offset)
            )
        ).scalars()
        return [
            {
                "id": m.id,
                "sender": m.sender,
                "sent_at": m.sent_at,
                "text": self._c.open(user_id, m.id, "msg.body", m.body_enc),
                "flagged": m.flagged,
            }
            for m in rows
        ]

    # ----------------------------------------------------------------- analysis / suggestions
    async def _msgs(
        self, user_id: uuid.UUID, conv: MsgConversation, limit: int = 300
    ) -> list[an.Msg]:
        rows = list(
            (
                await self._s.execute(
                    select(MsgMessage)
                    .where(
                        MsgMessage.conversation_id == conv.id,
                        MsgMessage.user_id == user_id,
                        MsgMessage.expires_at > self.now(),
                    )
                    .order_by(MsgMessage.sent_at.desc())
                    .limit(limit)
                )
            ).scalars()
        )
        return [
            an.Msg(m.sender, m.sent_at, self._c.open(user_id, m.id, "msg.body", m.body_enc))
            for m in reversed(rows)
        ]

    async def analysis(
        self, user_id: uuid.UUID, conv_id: uuid.UUID, *, for_agent: bool = False
    ) -> dict[str, Any]:
        conv = await self.conversation(user_id, conv_id)
        if for_agent and not conv.shared_with_assistant:
            raise MessagingError(
                "not_shared", "this conversation is not shared with the assistant", 403
            )
        msgs = await self._msgs(user_id, conv)
        real = conv.timestamps_reliable  # single source of truth (set at import)
        result = an.analyse(msgs, self.now(), has_real_timestamps=real)
        flagged = (
            await self._s.execute(
                select(func.count())
                .select_from(MsgMessage)
                .where(MsgMessage.conversation_id == conv.id, MsgMessage.flagged.is_(True))
            )
        ).scalar_one()
        result["instruction_like_messages"] = (
            flagged  # such text is data: it is never executed or obeyed
        )
        result["conversation_id"], result["platform"] = conv.id, conv.platform
        return result

    async def suggestions(
        self,
        user_id: uuid.UUID,
        conv_id: uuid.UUID,
        *,
        tone: str | None = None,
        for_agent: bool = False,
    ) -> dict[str, Any]:
        conv = await self.conversation(user_id, conv_id)
        a = await self.analysis(user_id, conv_id, for_agent=for_agent)
        rel = (
            (await self.contact(user_id, conv.contact_id)).relationship
            if conv.contact_id
            else "other"
        )
        out = an.suggest_replies(a, tone or conv.tone_pref, rel)
        out["conversation_id"] = conv.id
        return out

    async def inbox(
        self, user_id: uuid.UUID, *, limit: int = 50, offset: int = 0, shared_only: bool = False
    ) -> tuple[list[dict[str, Any]], int]:
        """One page of the owner's inbox with reply status, in a CONSTANT number of queries (page, total, latest
        message metadata, bodies for shared conversations only, flag counts): no per-row analysis.

        Privacy rule enforced HERE (server-side, read fresh from the database on every request): everything derived
        from message CONTENT - `last_message_intent` and `flagged_messages` - is returned only for conversations the
        user explicitly shared with Abu. For an unshared conversation those are null and its message bodies are
        never loaded or decrypted. `needs_reply`/`last_sender`/`waiting_hours` come from sender and timestamp
        metadata only. No message text is ever returned."""
        base = select(MsgConversation).where(MsgConversation.user_id == user_id)
        if shared_only:
            base = base.where(MsgConversation.shared_with_assistant.is_(True))
        total = (
            await self._s.execute(select(func.count()).select_from(base.subquery()))
        ).scalar_one()
        convs = list(
            (
                await self._s.execute(
                    base.order_by(
                        MsgConversation.last_message_at.desc().nulls_last(), MsgConversation.id
                    )
                    .limit(limit)
                    .offset(offset)
                )
            ).scalars()
        )
        if not convs:
            return [], total
        ids = [c.id for c in convs]
        now = self.now()
        # Latest unexpired message per conversation: METADATA ONLY (no body column is selected here).
        latest = {
            row.conversation_id: row
            for row in (
                await self._s.execute(
                    select(
                        MsgMessage.id,
                        MsgMessage.conversation_id,
                        MsgMessage.sender,
                        MsgMessage.sent_at,
                    )
                    .where(
                        MsgMessage.conversation_id.in_(ids),
                        MsgMessage.user_id == user_id,
                        MsgMessage.expires_at > now,
                    )
                    .ext(distinct_on(MsgMessage.conversation_id))
                    .order_by(MsgMessage.conversation_id, MsgMessage.sent_at.desc(), MsgMessage.id)
                )
            ).all()
        }
        shared_ids = {c.id for c in convs if c.shared_with_assistant}
        need_intent = {
            cid: row.id for cid, row in latest.items() if cid in shared_ids and row.sender == "them"
        }
        intents: dict[uuid.UUID, str] = {}
        if need_intent:  # bodies are loaded ONLY for shared conversations whose last message is from the other party
            for mid, cid, blob in (
                await self._s.execute(
                    select(MsgMessage.id, MsgMessage.conversation_id, MsgMessage.body_enc).where(
                        MsgMessage.id.in_(list(need_intent.values())), MsgMessage.user_id == user_id
                    )
                )
            ).all():
                intents[cid] = an.classify_intent(self._c.open(user_id, mid, "msg.body", blob))
        flagged: dict[uuid.UUID, int] = {}
        if shared_ids:
            flagged = {
                cid: n
                for cid, n in (
                    await self._s.execute(
                        select(MsgMessage.conversation_id, func.count())
                        .where(
                            MsgMessage.conversation_id.in_(list(shared_ids)),
                            MsgMessage.user_id == user_id,
                            MsgMessage.flagged.is_(True),
                        )
                        .group_by(MsgMessage.conversation_id)
                    )
                ).all()
            }
        out: list[dict[str, Any]] = []
        for c in convs:
            last = latest.get(c.id)
            needs_reply = last is not None and last.sender == "them"
            shared = c.shared_with_assistant
            out.append(
                {
                    "id": c.id,
                    "slug": c.slug,
                    "platform": c.platform,
                    "title": self._c.open(c.user_id, c.id, "msg.conv.title", c.title_enc)
                    if c.title_enc
                    else None,
                    "contact_id": c.contact_id,
                    "shared_with_assistant": shared,
                    "message_count": c.message_count,
                    "last_message_at": c.last_message_at,
                    "last_sender": last.sender if last else None,
                    "needs_reply": needs_reply,
                    "waiting_hours": round((now - last.sent_at).total_seconds() / 3600, 1)
                    if needs_reply and last is not None and c.timestamps_reliable
                    else None,
                    "timestamps_reliable": c.timestamps_reliable,
                    "last_message_intent": intents.get(c.id) if shared else None,
                    "flagged_messages": flagged.get(c.id, 0) if shared else None,
                }
            )
        return out, total

    async def purge_expired(self) -> int:
        res = await self._s.execute(delete(MsgMessage).where(MsgMessage.expires_at <= self.now()))
        return int(res.rowcount or 0)  # type: ignore[attr-defined]

    async def erase(self, user_id: uuid.UUID) -> None:
        for model in (MsgMessage, MsgConversation, MsgContact):
            await self._s.execute(delete(model).where(model.user_id == user_id))
