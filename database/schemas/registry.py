"""Model Registry and Agent Registry tables.

These are system-level configuration tables (not user-owned data), so they carry no user_id.
User-owned tables (memory, finance, messaging, ...) arrive in later milestones with a user_id.
"""

import enum
import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from database.schemas.base import Base
from database.schemas.columns import created_at_col, enum_col, uuid_pk


class DataClass(enum.IntEnum):
    """Sensitivity of data in a request. Higher value = more sensitive."""

    SYNTHETIC = 0  # fabricated test data
    NON_SENSITIVE = 1  # public / low-risk personal content
    SENSITIVE = 2  # financial records, private messages, anything about real people


class Tri(enum.StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class ApprovalStatus(enum.StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ProviderPolicy(Base):
    __tablename__ = "model_provider_policies"

    id: Mapped[uuid.UUID] = uuid_pk()
    provider: Mapped[str] = mapped_column(String(64), unique=True)
    retention_summary: Mapped[str] = mapped_column(Text)
    trains_on_data: Mapped[Tri] = mapped_column(enum_col(Tri, "tri_trains"), default=Tri.UNKNOWN)
    human_review: Mapped[Tri] = mapped_column(enum_col(Tri, "tri_review"), default=Tri.UNKNOWN)
    # Highest data class this provider is approved to receive. Defaults to synthetic only.
    max_data_class: Mapped[DataClass] = mapped_column(
        enum_col(DataClass, "data_class"), default=DataClass.SYNTHETIC
    )
    source_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = created_at_col()


class RegisteredModel(Base):
    __tablename__ = "models"
    __table_args__ = (
        # Defence in depth: the database itself refuses an enabled-but-unapproved model.
        CheckConstraint(
            "NOT enabled OR approval_status = 'approved'", name="enabled_requires_approval"
        ),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    name: Mapped[str] = mapped_column(String(128), unique=True)
    policy_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("model_provider_policies.id"))
    # Exact identifier at the provider, e.g. "gemini-3.1-flash-lite".
    provider_model_id: Mapped[str] = mapped_column(String(256))
    # The model_name configured in LiteLLM that fronts this model.
    gateway_model_name: Mapped[str] = mapped_column(String(256))
    version: Mapped[str | None] = mapped_column(String(64))
    context_limit: Mapped[int | None] = mapped_column(Integer)
    max_output_tokens: Mapped[int | None] = mapped_column(Integer)
    rpm_limit: Mapped[int | None] = mapped_column(Integer)
    rpd_limit: Mapped[int | None] = mapped_column(Integer)
    rate_limits_verified: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false")
    )
    availability_verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approval_status: Mapped[ApprovalStatus] = mapped_column(
        enum_col(ApprovalStatus, "approval_status"),
        default=ApprovalStatus.PENDING,
        server_default="pending",
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=False, server_default=text("false"))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = created_at_col()
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    policy: Mapped[ProviderPolicy] = relationship(lazy="joined")
    capabilities: Mapped[list["ModelCapability"]] = relationship(
        cascade="all, delete-orphan", lazy="selectin"
    )
    pricing: Mapped["ModelPricing | None"] = relationship(
        cascade="all, delete-orphan", lazy="joined", uselist=False
    )


class ModelCapability(Base):
    __tablename__ = "model_capabilities"

    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), primary_key=True
    )
    capability: Mapped[str] = mapped_column(String(64), primary_key=True)


class ModelPricing(Base):
    __tablename__ = "model_pricing"
    __table_args__ = (
        CheckConstraint("input_per_million >= 0 AND output_per_million >= 0", name="non_negative"),
    )

    model_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("models.id", ondelete="CASCADE"), primary_key=True
    )
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    input_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    output_per_million: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    source_url: Mapped[str | None] = mapped_column(Text)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    @property
    def is_free(self) -> bool:
        return self.input_per_million == 0 and self.output_per_million == 0


class ModelRoute(Base):
    """Logical alias -> ordered candidate models. Agents ask for aliases, never model IDs."""

    __tablename__ = "model_routes"
    __table_args__ = (
        UniqueConstraint("alias", "model_id"),
        UniqueConstraint("alias", "priority"),
    )

    id: Mapped[uuid.UUID] = uuid_pk()
    alias: Mapped[str] = mapped_column(String(64))
    model_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("models.id", ondelete="CASCADE"))
    priority: Mapped[int] = mapped_column(Integer)  # lower = tried first
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
