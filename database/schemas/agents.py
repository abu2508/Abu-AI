"""Agent Registry: agents, immutable-once-active versions, and per-version permissions.

Everything is default-deny: a version can use only the model aliases, tools and memory
namespaces explicitly granted to it, and only the ACTIVE version of an ENABLED agent runs.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.schemas.base import Base
from database.schemas.columns import created_at_col, enum_col, uuid_pk


class VersionState(enum.StrEnum):
    DRAFT = "draft"  # editable, never runs
    ACTIVE = "active"  # frozen; the only version that runs (max one per agent)
    RETIRED = "retired"  # frozen; kept for audit/rollback via clone


class MemoryMode(enum.StrEnum):
    READ = "read"
    READ_WRITE = "read_write"


class Agent(Base):
    __tablename__ = "agents"

    agent_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    created_at: Mapped[datetime] = created_at_col()


class AgentVersion(Base):
    __tablename__ = "agent_versions"
    __table_args__ = (
        UniqueConstraint("agent_id", "version"),
        # At most one ACTIVE version per agent, enforced by the database.
        Index(
            "uq_agent_versions_one_active",
            "agent_id",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
        CheckConstraint("max_steps BETWEEN 1 AND 50", name="steps_bounds"),
        CheckConstraint("timeout_seconds BETWEEN 1 AND 300", name="timeout_bounds"),
        CheckConstraint("max_run_input_tokens BETWEEN 1 AND 200000", name="input_tokens_bounds"),
        CheckConstraint("max_run_output_tokens BETWEEN 1 AND 100000", name="output_tokens_bounds"),
        CheckConstraint("max_run_cost_inr BETWEEN 0 AND 50", name="cost_bounds"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    state: Mapped[VersionState] = mapped_column(
        enum_col(VersionState, "version_state"),
        default=VersionState.DRAFT,
        server_default="draft",
    )
    # Execution limits (version-specific). Cost is per run, in INR; default 0 = free models only.
    max_steps: Mapped[int] = mapped_column(Integer, default=8, server_default="8")
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=60, server_default="60")
    max_run_input_tokens: Mapped[int] = mapped_column(
        Integer, default=20_000, server_default="20000"
    )
    max_run_output_tokens: Mapped[int] = mapped_column(
        Integer, default=4_000, server_default="4000"
    )
    max_run_cost_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal(0), server_default="0"
    )
    # Non-security, version-specific configuration (prompts, tunables). Frozen with the version.
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    created_at: Mapped[datetime] = created_at_col()
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    model_aliases: Mapped[list["AgentVersionModelAlias"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    tools: Mapped[list["AgentVersionTool"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    memory: Mapped[list["AgentVersionMemory"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )


class AgentVersionModelAlias(Base):
    __tablename__ = "agent_version_model_aliases"

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="CASCADE"), primary_key=True
    )
    alias: Mapped[str] = mapped_column(String(64), primary_key=True)


class AgentVersionTool(Base):
    __tablename__ = "agent_version_tools"

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="CASCADE"), primary_key=True
    )
    tool_name: Mapped[str] = mapped_column(String(64), primary_key=True)
    requires_approval: Mapped[bool] = mapped_column(Boolean, default=True)


class AgentVersionMemory(Base):
    __tablename__ = "agent_version_memory"

    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="CASCADE"), primary_key=True
    )
    namespace: Mapped[str] = mapped_column(String(128), primary_key=True)
    mode: Mapped[MemoryMode] = mapped_column(enum_col(MemoryMode, "memory_mode"))


class ToolApproval(Base):
    """A human approval bound to ONE exact tool call (args hash), single use, short-lived."""

    __tablename__ = "tool_approvals"

    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id", ondelete="CASCADE"))
    version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("agent_versions.id", ondelete="CASCADE")
    )
    tool_name: Mapped[str] = mapped_column(String(64))
    args_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = created_at_col()
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
