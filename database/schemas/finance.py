"""Finance domain tables (Milestone 7a). All rows are user-owned (ON DELETE CASCADE).

Free text a person typed or a bank sent (labels, descriptions, merchant text) is envelope-encrypted per
field (`*_enc`). Amounts, dates, categories and keyed hashes stay plaintext columns on purpose: they are
needed for constraints, indexes and exact deterministic arithmetic. Raw notifications, statements and
uploaded files are NEVER stored - only keyed hashes used for duplicate detection.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk

Money = Numeric(14, 2)


def _user() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class FinAccount(Base):
    __tablename__ = "fin_accounts"
    __table_args__ = (
        CheckConstraint("kind IN ('bank','credit_card','cash','wallet')", name="kind_valid"),
        CheckConstraint("last4 IS NULL OR last4 ~ '^[0-9]{4}$'", name="last4_valid"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    kind: Mapped[str] = mapped_column(String(16))
    label_enc: Mapped[bytes] = mapped_column(LargeBinary)
    institution: Mapped[str | None] = mapped_column(String(32))  # short code, e.g. "hdfc"
    last4: Mapped[str | None] = mapped_column(String(4))
    opening_balance: Mapped[Decimal] = mapped_column(Money, default=Decimal(0), server_default="0")
    credit_limit: Mapped[Decimal | None] = mapped_column(Money)
    statement_day: Mapped[int | None] = mapped_column(Integer)
    due_day: Mapped[int | None] = mapped_column(Integer)
    auto_confirm: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    archived_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class FinImportBatch(Base):
    __tablename__ = "fin_import_batches"
    __table_args__ = (UniqueConstraint("user_id", "file_hash", name="uq_fin_batch_user_file"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin_accounts.id", ondelete="SET NULL")
    )
    source: Mapped[str] = mapped_column(String(16))  # csv | xlsx | pdf
    file_hash: Mapped[str | None] = mapped_column(
        String(64)
    )  # keyed hash; the file is never stored
    parsed: Mapped[int] = mapped_column(Integer, default=0)
    imported: Mapped[int] = mapped_column(Integer, default=0)
    duplicates: Mapped[int] = mapped_column(Integer, default=0)
    reconciled: Mapped[int] = mapped_column(Integer, default=0)
    rejected: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = created_at_col()


class FinTransaction(Base):
    __tablename__ = "fin_transactions"
    __table_args__ = (
        CheckConstraint("direction IN ('debit','credit')", name="direction_valid"),
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "status IN ('confirmed','pending_review','duplicate_suspect','rejected')",
            name="status_valid",
        ),
        CheckConstraint(
            "source IN ('manual','voice','sms','email','shortcut','share','csv','xlsx','pdf')",
            name="source_valid",
        ),
        # S2.1A classification safety. Every new column is nullable and unset on historical rows (no backfill).
        CheckConstraint(
            "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
            name="classification_confidence_range",
        ),
        CheckConstraint(
            "review_reason IS NULL OR review_reason IN "
            "('unknown_credit','unlinked_transfer','ambiguous_transfer','emi_unverified')",
            name="review_reason_valid",
        ),
        CheckConstraint(
            "review_reason IS NULL OR status = 'pending_review'",
            name="review_reason_requires_pending",
        ),
        CheckConstraint(
            "transfer_peer_id IS NULL OR transfer_peer_id <> id", name="transfer_peer_not_self"
        ),
        CheckConstraint(
            "transfer_peer_id IS NULL OR (kind = 'transfer' AND status = 'confirmed')",
            name="transfer_peer_requires_transfer",
        ),
        CheckConstraint(
            "(emi_principal IS NULL) = (emi_interest IS NULL)", name="emi_split_complete"
        ),
        CheckConstraint(
            "emi_principal IS NULL OR (emi_principal >= 0 AND emi_interest >= 0)",
            name="emi_split_non_negative",
        ),
        CheckConstraint(
            "emi_principal IS NULL OR emi_principal + emi_interest = amount", name="emi_split_sum"
        ),
        CheckConstraint(
            "emi_principal IS NULL OR (kind = 'emi' AND direction = 'debit')",
            name="emi_split_requires_emi_debit",
        ),
        CheckConstraint("loan_id IS NULL OR emi_principal IS NOT NULL", name="loan_requires_split"),
        Index(
            "uq_fin_txn_transfer_peer",
            "transfer_peer_id",
            unique=True,
            postgresql_where="transfer_peer_id IS NOT NULL",
        ),
        Index("ix_fin_txn_user_date", "user_id", "txn_date"),
        Index("ix_fin_txn_user_dedupe", "user_id", "dedupe_key"),
        # The same raw notification/statement row can never be ingested twice.
        Index(
            "uq_fin_txn_user_source_hash",
            "user_id",
            "source_hash",
            unique=True,
            postgresql_where="source_hash IS NOT NULL",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    account_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("fin_accounts.id", ondelete="CASCADE"))
    txn_date: Mapped[date] = mapped_column(Date)
    amount: Mapped[Decimal] = mapped_column(Money)
    direction: Mapped[str] = mapped_column(String(8))
    category: Mapped[str | None] = mapped_column(String(32))
    kind: Mapped[str] = mapped_column(String(16), default="expense", server_default="expense")
    description_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
    merchant_hash: Mapped[str | None] = mapped_column(String(64))
    reference_hash: Mapped[str | None] = mapped_column(String(64))
    dedupe_key: Mapped[str] = mapped_column(String(64))
    source_hash: Mapped[str | None] = mapped_column(String(64))
    source: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(20), default="confirmed", server_default="confirmed")
    confidence: Mapped[Decimal] = mapped_column(
        Numeric(3, 2), default=Decimal(1), server_default="1"
    )
    duplicate_of: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin_transactions.id", ondelete="SET NULL")
    )
    batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin_import_batches.id", ondelete="SET NULL")
    )
    reconciled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()
    # --- S2.1A. NULL classification_confidence = a historical row: never routed, linked, demoted or re-reported.
    classification_confidence: Mapped[Decimal | None] = mapped_column(Numeric(3, 2))
    review_reason: Mapped[str | None] = mapped_column(
        String(24)
    )  # only while status = pending_review
    # The other leg of an own-account transfer (reciprocal, enforced by a deferred trigger; see migration 0012).
    transfer_peer_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin_transactions.id", ondelete="SET NULL")
    )
    loan_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fin_loans.id", ondelete="SET NULL")
    )
    emi_principal: Mapped[Decimal | None] = mapped_column(Money)
    emi_interest: Mapped[Decimal | None] = mapped_column(Money)


class FinMerchantRule(Base):
    """The user's own merchant -> category choices. Keyed by a hash: no merchant text stored."""

    __tablename__ = "fin_merchant_rules"
    __table_args__ = (
        UniqueConstraint("user_id", "merchant_hash", name="uq_fin_rule_user_merchant"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    merchant_hash: Mapped[str] = mapped_column(String(64))
    category: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[datetime] = created_at_col()


class FinLoan(Base):
    __tablename__ = "fin_loans"
    __table_args__ = (
        CheckConstraint(
            "principal > 0 AND tenor_months > 0 AND annual_rate_pct >= 0", name="loan_valid"
        ),
        CheckConstraint("status IN ('active','closed')", name="status_valid"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    label_enc: Mapped[bytes] = mapped_column(LargeBinary)
    principal: Mapped[Decimal] = mapped_column(Money)
    annual_rate_pct: Mapped[Decimal] = mapped_column(Numeric(6, 3))
    tenor_months: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    emi_amount: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(8), default="active", server_default="active")
    created_at: Mapped[datetime] = created_at_col()


class FinSubscription(Base):
    __tablename__ = "fin_subscriptions"
    __table_args__ = (
        CheckConstraint("period IN ('weekly','monthly','yearly')", name="period_valid"),
        CheckConstraint("status IN ('active','paused','cancelled')", name="status_valid"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    label_enc: Mapped[bytes] = mapped_column(LargeBinary)
    merchant_hash: Mapped[str | None] = mapped_column(String(64))
    amount: Mapped[Decimal] = mapped_column(Money)
    period: Mapped[str] = mapped_column(String(8))
    next_due: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(10), default="active", server_default="active")
    origin: Mapped[str] = mapped_column(String(10), default="manual", server_default="manual")
    created_at: Mapped[datetime] = created_at_col()


class FinBudget(Base):
    __tablename__ = "fin_budgets"
    __table_args__ = (
        UniqueConstraint("user_id", "category", name="uq_fin_budget_user_category"),
        CheckConstraint("monthly_limit > 0", name="limit_positive"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    category: Mapped[str] = mapped_column(String(32))  # "*" = overall monthly spending budget
    monthly_limit: Mapped[Decimal] = mapped_column(Money)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
