"""Long-term memory tables. Content is ALWAYS encrypted (core/memory/crypto.py); plaintext columns
hold only classification metadata. LangGraph checkpoints live in a separate schema (lg_checkpoints),
created by LangGraph's own setup; agent_threads maps each thread to its owner for isolation,
retention and deletion.
"""

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk

# Dimension of memory_embeddings.embedding. Changing the embedding model's dimension requires a
# migration (new column/table) AND an explicit re-embed; the application refuses to embed on a
# mismatch rather than guess.
EMBEDDING_DIM = 256

MEMORY_TYPES = ("episodic", "semantic", "procedural", "structured")
STATUSES = ("proposed", "active", "superseded")
SENSITIVITIES = ("low", "personal", "sensitive", "third_party")
SOURCE_KINDS = (
    "user_created",
    "user_statement",
    "agent_inference",
    "third_party_message",
    "tool_result",
    "system",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AgentThread(Base):
    """Ownership of a LangGraph thread (working memory). Deleting the row's thread removes every
    historical checkpoint of it (see core/memory/threads.py)."""

    __tablename__ = "agent_threads"
    __table_args__ = (Index("ix_agent_threads_expires_at", "expires_at"),)

    thread_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="CASCADE"))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MemoryRecord(Base):
    __tablename__ = "memory_records"
    __table_args__ = (
        CheckConstraint(_in("memory_type", MEMORY_TYPES), name="type_valid"),
        CheckConstraint(_in("status", STATUSES), name="status_valid"),
        CheckConstraint(_in("sensitivity", SENSITIVITIES), name="sensitivity_valid"),
        CheckConstraint(_in("source_kind", SOURCE_KINDS), name="source_valid"),
        CheckConstraint(
            _in("embedding_state", ("none", "approved", "embedded")), name="embedding_state_valid"
        ),
        CheckConstraint("confidence BETWEEN 0 AND 1", name="confidence_range"),
        # Injection guard at the database level: text that came from a third party can never become
        # an agent RULE (procedural memory), whatever the application layer does.
        CheckConstraint(
            "NOT (source_kind = 'third_party_message' AND memory_type = 'procedural')",
            name="no_third_party_procedural",
        ),
        # Transactional/structured data is queried exactly with SQL, never embedded.
        CheckConstraint(
            "memory_type <> 'structured' OR embedding_state = 'none'",
            name="structured_not_embedded",
        ),
        # Third-party-derived text may only be embedded after the user explicitly confirmed it.
        CheckConstraint(
            "source_kind <> 'third_party_message' OR embedding_state = 'none' OR confirmed_at IS NOT NULL",
            name="third_party_embedding_needs_confirmation",
        ),
        CheckConstraint(
            "status <> 'active' OR confirmed_at IS NOT NULL OR created_by_kind <> 'user'",
            name="user_records_confirmed",
        ),
        Index("ix_memory_records_user_ns_status", "user_id", "namespace", "status"),
        Index("ix_memory_records_user_expiry", "user_id", "retention_expires_at"),
        # At most one ACTIVE record per (user, namespace, key): a changed fact must go through the
        # supersede workflow, never a silent duplicate/overwrite.
        Index(
            "uq_memory_records_active_key",
            "user_id",
            "namespace",
            "key",
            unique=True,
            postgresql_where=text("status = 'active' AND key IS NOT NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    namespace: Mapped[str] = mapped_column(String(128))
    memory_type: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(16))
    key: Mapped[str | None] = mapped_column(String(128))
    schema_name: Mapped[str | None] = mapped_column(String(64))
    # Envelope-encrypted JSON {"v":1,"text":...,"data":...}. embed_text_enc is the separately
    # approved, PII-redacted summary that is the ONLY thing that may ever be embedded.
    content_enc: Mapped[bytes] = mapped_column(LargeBinary)
    embed_text_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    embedding_state: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    sensitivity: Mapped[str] = mapped_column(String(16))
    source_kind: Mapped[str] = mapped_column(String(24))
    source_ref: Mapped[str | None] = mapped_column(String(128))  # opaque reference, never content
    source_thread_id: Mapped[str | None] = mapped_column(
        ForeignKey("agent_threads.thread_id", ondelete="SET NULL")
    )
    source_execution_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("executions.id", ondelete="SET NULL")
    )
    created_by: Mapped[str] = mapped_column(String(64))
    created_by_kind: Mapped[str] = mapped_column(String(8))  # agent | user | system
    confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2))
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("memory_records.id", ondelete="SET NULL")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(  # a summary derived from another record
        ForeignKey("memory_records.id", ondelete="CASCADE")
    )
    flags: Mapped[list[str]] = mapped_column(JSONB, server_default=text("'[]'::jsonb"))
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    last_accessed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class MemoryEmbedding(Base):
    """pgvector similarity index over APPROVED, REDACTED summaries only.

    Embeddings are sensitive (they leak information about the text). They are stored UNENCRYPTED
    because ciphertext cannot be searched: column-level encryption of the vector would prevent
    ordinary pgvector distance queries. Mitigations: disk/backup encryption, per-user filtering,
    cascade deletion, access logging, and never returning vectors from any API.
    """

    __tablename__ = "memory_embeddings"
    __table_args__ = (
        Index(
            "ix_memory_embeddings_hnsw",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
    )

    memory_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("memory_records.id", ondelete="CASCADE"), primary_key=True
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    embedder_id: Mapped[str] = mapped_column(String(64))
    embedding: Mapped[Any] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[datetime] = created_at_col()


class MemoryAccessLog(Base):
    """Who read/searched/changed which memory, when. Metadata only (never content)."""

    __tablename__ = "memory_access_logs"
    __table_args__ = (Index("ix_memory_access_logs_user_time", "user_id", "occurred_at"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    actor_type: Mapped[str] = mapped_column(String(8))  # agent | user | system | admin
    actor_id: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(16))
    # No FK on purpose; scrubbed (set NULL) when the record is deleted.
    memory_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    namespace: Mapped[str | None] = mapped_column(String(128))
    execution_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    result_count: Mapped[int] = mapped_column(Integer, default=0)
