"""Orchestrator runs: idempotency claims and request history.

The user's REQUEST TEXT is never stored (only a keyed hash, to detect an idempotency key reused with
a different body). The generated response is kept only so an idempotent retry can be replayed: it is
envelope-encrypted (same keyring as memory) and expires after a short TTL, after which the ciphertext
is scrubbed and only metadata remains. Execution detail (calls, tokens, cost) lives in executions.
"""

import uuid
from datetime import datetime

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Index, LargeBinary, String, text
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk


class OrchestratorRun(Base):
    __tablename__ = "orchestrator_runs"
    __table_args__ = (
        CheckConstraint("status IN ('running','completed','failed')", name="status_valid"),
        # One claim per (user, idempotency key). Requests without a key are never constrained.
        Index(
            "uq_orchestrator_runs_user_key",
            "user_id",
            "idempotency_key_hash",
            unique=True,
            postgresql_where=text("idempotency_key_hash IS NOT NULL"),
        ),
        Index("ix_orchestrator_runs_user_created", "user_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    idempotency_key_hash: Mapped[str | None] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    origin: Mapped[str] = mapped_column(String(16), default="user", server_default="user")
    intent: Mapped[str | None] = mapped_column(String(32))
    agent_id: Mapped[str | None] = mapped_column(String(64))
    workflow: Mapped[str | None] = mapped_column(String(64))
    outcome: Mapped[str | None] = mapped_column(
        String(24)
    )  # completed | clarification_needed | unsupported | failed
    error_code: Mapped[str | None] = mapped_column(String(48))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL")
    )
    response_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    response_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
