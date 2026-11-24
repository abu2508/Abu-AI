"""Messaging domain tables (Milestone 7b). Conversations exist only because the USER imported or shared
them. Message bodies are envelope-encrypted, expire after the conversation's retention period, are never
embedded and never copied into memory. There is no table, column or route for sending anything."""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk

PLATFORMS = "('whatsapp','instagram','telegram','sms','imessage','other')"


def _user() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class MsgContact(Base):
    __tablename__ = "msg_contacts"
    __table_args__ = (
        CheckConstraint(
            "relationship IN ('friend','family','partner','colleague','acquaintance','other')",
            name="relationship_valid",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    name_enc: Mapped[bytes] = mapped_column(LargeBinary)
    relationship: Mapped[str] = mapped_column(String(16), default="other", server_default="other")
    notes_enc: Mapped[bytes | None] = mapped_column(LargeBinary)  # written by the user
    created_at: Mapped[datetime] = created_at_col()


class MsgConversation(Base):
    __tablename__ = "msg_conversations"
    __table_args__ = (
        CheckConstraint(f"platform IN {PLATFORMS}", name="platform_valid"),
        CheckConstraint("tone_pref IN ('casual','warm','formal','brief')", name="tone_valid"),
        CheckConstraint("retention_days BETWEEN 1 AND 365", name="retention_valid"),
        UniqueConstraint("user_id", "slug", name="uq_msg_conv_user_slug"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    slug: Mapped[str] = mapped_column(String(16))  # opaque id used in the memory namespace
    platform: Mapped[str] = mapped_column(String(16))
    contact_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("msg_contacts.id", ondelete="SET NULL")
    )
    title_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    # False when message timestamps are synthetic (a 'labelled' paste has none): response times and waiting
    # hours are then never reported.
    timestamps_reliable: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    shared_with_assistant: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    tone_pref: Mapped[str] = mapped_column(String(8), default="casual", server_default="casual")
    retention_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")
    message_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class MsgMessage(Base):
    __tablename__ = "msg_messages"
    __table_args__ = (
        CheckConstraint("sender IN ('me','them')", name="sender_valid"),
        UniqueConstraint("conversation_id", "content_hash", name="uq_msg_conv_content"),
        Index("ix_msg_messages_conv_sent", "conversation_id", "sent_at"),
        Index("ix_msg_messages_expires", "expires_at"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("msg_conversations.id", ondelete="CASCADE")
    )
    sender: Mapped[str] = mapped_column(String(4))
    sent_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    body_enc: Mapped[bytes] = mapped_column(LargeBinary)
    content_hash: Mapped[str] = mapped_column(String(64))
    flagged: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
