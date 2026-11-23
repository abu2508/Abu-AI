"""Types shared by the memory service, API and agent facade."""

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from core.permissions.runtime import RunContext

MemoryType = Literal["episodic", "semantic", "procedural", "structured"]
Sensitivity = Literal["low", "personal", "sensitive", "third_party"]
SourceKind = Literal[
    "user_created",
    "user_statement",
    "agent_inference",
    "third_party_message",
    "tool_result",
    "system",
]
SENSITIVITY_ORDER = {"low": 0, "personal": 1, "sensitive": 2, "third_party": 3}


class MemoryError_(Exception):
    """Base class for memory failures that are safe to show (never contain memory content)."""


class MemoryNotFoundError(MemoryError_):
    """Unknown id OR not yours: deliberately indistinguishable."""


class PolicyViolationError(MemoryError_):
    """The proposal breaks a hard rule (e.g. third-party text as an agent rule, raw content retention)."""


class InvalidStateError(MemoryError_):
    pass


@dataclass(frozen=True)
class Caller:
    """WHO is acting, with the user whose data it is fixed by authentication - never by input."""

    kind: Literal["user", "agent", "admin", "system"]
    user_id: uuid.UUID
    agent_id: str | None = None
    ctx: RunContext | None = None

    @classmethod
    def user(cls, user_id: uuid.UUID) -> "Caller":
        return cls("user", user_id)

    @classmethod
    def admin(cls, user_id: uuid.UUID) -> "Caller":
        return cls("admin", user_id)

    @classmethod
    def system(cls, user_id: uuid.UUID) -> "Caller":
        return cls("system", user_id)

    @classmethod
    def agent(cls, ctx: RunContext) -> "Caller":
        return cls("agent", ctx.user_id, ctx.agent_id, ctx)

    @property
    def actor_id(self) -> str:
        return self.agent_id or str(self.user_id)

    @property
    def execution_id(self) -> uuid.UUID | None:
        return self.ctx.execution_id if self.ctx else None


class MemoryProposal(BaseModel):
    """What an agent (an LLM) or the user wants to remember. Validated before anything is stored."""

    model_config = ConfigDict(extra="forbid")

    namespace: str
    memory_type: MemoryType
    text: str | None = Field(default=None, max_length=4000)
    data: dict[str, Any] | None = None  # structured memory only
    schema_name: str | None = Field(default=None, max_length=64)
    key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
    # The ONLY text that may ever be embedded: separately approved and PII-redacted.
    embed_text: str | None = Field(default=None, max_length=1000)
    sensitivity: Sensitivity = "personal"
    source_kind: SourceKind
    source_ref: str | None = Field(default=None, max_length=128, pattern=r"^[A-Za-z0-9_.:/-]+$")
    source_thread_id: str | None = Field(default=None, max_length=64)
    confidence: float = Field(default=0.5, ge=0, le=1)
    retention_days: int | None = Field(default=None, ge=1, le=3650)  # may only SHORTEN the default
    # A summary derived from another memory: deleting the parent deletes this too.
    derived_from: uuid.UUID | None = None


@dataclass(frozen=True)
class ProposalResult:
    id: uuid.UUID
    status: str  # proposed | active
    needs_confirmation: bool
    reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)
    supersedes_id: uuid.UUID | None = None


@dataclass(frozen=True)
class MemoryView:
    """A decrypted record for an authorised reader. Embeddings are never part of it."""

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
    similarity: float | None = None


@dataclass(frozen=True)
class DeletionReceipt:
    """Counts and ids only - never content."""

    records: int
    embeddings: int
    threads: int
    access_log_rows_scrubbed: int
    record_ids: list[uuid.UUID] = field(default_factory=list)


@dataclass(frozen=True)
class ContextItem:
    id: uuid.UUID
    namespace: str
    memory_type: str
    text: str
    score: float
    similarity: float | None
    confirmed: bool
    source_kind: str
    confidence: float
    created_at: datetime


@dataclass(frozen=True)
class ContextBundle:
    items: list[ContextItem]
    rules: list[ContextItem]  # user-confirmed procedural memory, kept apart from data
    omitted: int
    similarity_basis: str  # "lexical-test" for the test embedder, else "semantic"
    tokens_estimate: int
