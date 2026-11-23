"""Governance state: kill switch, spending configuration and ledger, execution records.

kill_switch_state and spending_config are single-row tables (id = 1, enforced by CHECK; deletion is
refused by trigger). If either row is missing or unreadable the application FAILS CLOSED.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk

# Absolute ceiling for monthly paid spend, INR. Mirrored by DB CHECK constraints; raising it
# requires a migration, i.e. an explicit design decision.
ABSOLUTE_MONTHLY_CEILING_INR = Decimal("300")


class KillSwitchState(Base):
    __tablename__ = "kill_switch_state"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    active: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    reason: Mapped[str | None] = mapped_column(Text)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    activated_by_type: Mapped[str | None] = mapped_column(String(16))  # user | admin
    activated_by_user_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    activated_via: Mapped[str | None] = mapped_column(String(16))  # cli | api
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class SpendingConfig(Base):
    __tablename__ = "spending_config"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("monthly_budget_inr BETWEEN 0 AND 300", name="monthly_ceiling"),
        CheckConstraint("daily_budget_inr BETWEEN 0 AND 300", name="daily_ceiling"),
        CheckConstraint("daily_budget_inr <= monthly_budget_inr", name="daily_le_monthly"),
        CheckConstraint("usd_inr_rate IS NULL OR usd_inr_rate > 0", name="rate_positive"),
        CheckConstraint("utc_offset_minutes BETWEEN -720 AND 840", name="offset_bounds"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    # Paid-model spend limits. Default 0: nothing paid can be spent until an operator opts in.
    monthly_budget_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal(0), server_default="0"
    )
    daily_budget_inr: Mapped[Decimal] = mapped_column(
        Numeric(12, 4), default=Decimal(0), server_default="0"
    )
    paid_models_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    # Set automatically on unknown cost / possible charge / overrun; cleared only by the operator.
    paid_frozen: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    paid_frozen_reason: Mapped[str | None] = mapped_column(String(200))
    paid_frozen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # USD->INR, set explicitly by the operator (no placeholder). NULL => paid calls refused.
    usd_inr_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    rate_source: Mapped[str | None] = mapped_column(String(128))
    rate_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    rate_max_age_days: Mapped[int] = mapped_column(Integer, default=14, server_default="14")
    # Day/month rollover boundary. 330 = IST (no DST). Avoids a timezone-database dependency.
    utc_offset_minutes: Mapped[int] = mapped_column(Integer, default=330, server_default="330")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()")
    )


class Execution(Base):
    """One agent run. Metadata only: never prompts, completions or tool arguments."""

    __tablename__ = "executions"

    id: Mapped[uuid.UUID] = uuid_pk()
    trace_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    agent_id: Mapped[str] = mapped_column(ForeignKey("agents.agent_id"))
    agent_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("agent_versions.id"))
    agent_version: Mapped[int] = mapped_column(Integer)
    task_type: Mapped[str] = mapped_column(String(64))
    # running | succeeded | failed | blocked | limit_exceeded | timeout
    status: Mapped[str] = mapped_column(String(16), default="running", server_default="running")
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    call_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    total_input_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    total_output_tokens: Mapped[int] = mapped_column(BigInteger, default=0, server_default="0")
    total_estimated_cost_inr: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal(0), server_default="0"
    )
    total_confirmed_cost_inr: Mapped[Decimal] = mapped_column(
        Numeric(14, 6), default=Decimal(0), server_default="0"
    )


class ExecutionCall(Base):
    """One model-call attempt inside an execution (a fallback is a separate attempt)."""

    __tablename__ = "execution_calls"
    __table_args__ = (Index("ix_execution_calls_execution_id_step", "execution_id", "step"),)

    id: Mapped[uuid.UUID] = uuid_pk()
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id", ondelete="CASCADE"))
    step: Mapped[int] = mapped_column(Integer)
    attempt: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16))  # succeeded | failed | blocked
    failure_reason: Mapped[str | None] = mapped_column(String(64))
    model_alias: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str | None] = mapped_column(String(128))
    provider: Mapped[str | None] = mapped_column(String(64))
    provider_model_id: Mapped[str | None] = mapped_column(String(256))
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    # estimated = worst-case pre-call figure; confirmed = from provider usage/cost after the call.
    estimated_cost_inr: Mapped[Decimal] = mapped_column(Numeric(14, 6), default=Decimal(0))
    confirmed_cost_inr: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    cost_status: Mapped[str] = mapped_column(String(16))  # free | confirmed | unknown | none
    cost_source: Mapped[str | None] = mapped_column(String(24))  # gateway_reported | registry_price
    provider_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(14, 8))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    usd_inr_rate: Mapped[Decimal | None] = mapped_column(Numeric(12, 6))
    latency_ms: Mapped[float | None] = mapped_column(Float)
    fallback_used: Mapped[bool] = mapped_column(Boolean, default=False)
    fallback_from: Mapped[str | None] = mapped_column(String(128))
    ledger_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    policy_decisions: Mapped[dict[str, Any]] = mapped_column(
        JSONB, server_default=text("'{}'::jsonb")
    )
    started_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SpendLedger(Base):
    """The authoritative paid-spend ledger: one row per PAID provider attempt.

    reserved -> settled (confirmed cost) | released (never charged) ; reserved -> held (possibly
    charged / cost unknown: counted at the worst case until an operator resolves it).
    Rows are never deleted; settled/released rows are immutable (trigger).
    """

    __tablename__ = "spend_ledger"
    __table_args__ = (
        CheckConstraint("state IN ('reserved','held','settled','released')", name="state_valid"),
        CheckConstraint(
            "(state = 'settled') = (confirmed_inr IS NOT NULL)", name="confirmed_iff_settled"
        ),
        CheckConstraint("reserved_inr >= 0", name="reserved_non_negative"),
        Index("ix_spend_ledger_period_month_state", "period_month", "state"),
        Index("ix_spend_ledger_period_day_state", "period_day", "state"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    # Idempotency key: "<execution_id>:<step>:<attempt>". A retry of the same attempt reuses the row.
    call_key: Mapped[str] = mapped_column(String(160), unique=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("executions.id"), index=True)
    user_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True))
    agent_id: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(64))
    model_name: Mapped[str] = mapped_column(String(128))
    state: Mapped[str] = mapped_column(String(16), default="reserved")
    reserved_inr: Mapped[Decimal] = mapped_column(Numeric(14, 6))
    confirmed_inr: Mapped[Decimal | None] = mapped_column(Numeric(14, 6))
    currency: Mapped[str] = mapped_column(String(3), default="INR")
    usd_inr_rate: Mapped[Decimal] = mapped_column(Numeric(12, 6))
    period_day: Mapped[date] = mapped_column(Date)
    period_month: Mapped[date] = mapped_column(Date)  # first day of the month
    created_at: Mapped[datetime] = created_at_col()
    settled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    note: Mapped[str | None] = mapped_column(String(200))
