"""finance: classification confidence, review reasons, reciprocal transfer links and EMI splits (S2.1A)

Revision ID: 0012
Revises: 0011

Additive only. Every new column is NULLABLE and is left NULL on existing rows: there is NO backfill, so historical
transactions keep exactly the behaviour they had (a NULL classification_confidence marks a legacy row).

* classification_confidence  the confidence of the CLASSIFICATION (separate from the parse `confidence` column)
* review_reason              why a row waits for review (only while status = 'pending_review')
* transfer_peer_id           the other leg of an own-account transfer; one-to-one, reciprocal, same user, different
                             account, opposite direction, same amount (a DEFERRED constraint trigger proves it at COMMIT)
* loan_id, emi_principal/emi_interest   a RELIABLE EMI split; both parts or neither, non-negative, summing to the amount

Foreign keys are ON DELETE SET NULL (deleting a transaction never deletes its peer; the service then demotes the survivor).
"""

import sqlalchemy as sa
from alembic import op

revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None

_CHECKS = {
    "classification_confidence_range": "classification_confidence IS NULL OR (classification_confidence >= 0 AND classification_confidence <= 1)",
    "review_reason_valid": "review_reason IS NULL OR review_reason IN ('unknown_credit','unlinked_transfer','ambiguous_transfer','emi_unverified')",
    "review_reason_requires_pending": "review_reason IS NULL OR status = 'pending_review'",
    "transfer_peer_not_self": "transfer_peer_id IS NULL OR transfer_peer_id <> id",
    "transfer_peer_requires_transfer": "transfer_peer_id IS NULL OR (kind = 'transfer' AND status = 'confirmed')",
    "emi_split_complete": "(emi_principal IS NULL) = (emi_interest IS NULL)",
    "emi_split_non_negative": "emi_principal IS NULL OR (emi_principal >= 0 AND emi_interest >= 0)",
    "emi_split_sum": "emi_principal IS NULL OR emi_principal + emi_interest = amount",
    "emi_split_requires_emi_debit": "emi_principal IS NULL OR (kind = 'emi' AND direction = 'debit')",
    "loan_requires_split": "loan_id IS NULL OR emi_principal IS NOT NULL",
}


def upgrade() -> None:
    t = "fin_transactions"
    op.add_column(t, sa.Column("classification_confidence", sa.Numeric(3, 2), nullable=True))
    op.add_column(t, sa.Column("review_reason", sa.String(length=24), nullable=True))
    op.add_column(t, sa.Column("transfer_peer_id", sa.UUID(), nullable=True))
    op.add_column(t, sa.Column("loan_id", sa.UUID(), nullable=True))
    op.add_column(t, sa.Column("emi_principal", sa.Numeric(14, 2), nullable=True))
    op.add_column(t, sa.Column("emi_interest", sa.Numeric(14, 2), nullable=True))
    op.create_foreign_key(
        op.f("fk_fin_transactions_transfer_peer_id_fin_transactions"),
        t,
        t,
        ["transfer_peer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        op.f("fk_fin_transactions_loan_id_fin_loans"),
        t,
        "fin_loans",
        ["loan_id"],
        ["id"],
        ondelete="SET NULL",
    )
    for name, expr in _CHECKS.items():
        op.create_check_constraint(op.f(f"ck_fin_transactions_{name}"), t, expr)
    op.create_index(
        "uq_fin_txn_transfer_peer",
        t,
        ["transfer_peer_id"],
        unique=True,
        postgresql_where=sa.text("transfer_peer_id IS NOT NULL"),
    )
    # Reciprocity is a property of TWO rows, so it cannot be a CHECK. It is proven at COMMIT (DEFERRED), which lets the
    # service update both legs in one transaction in any order, while any one-sided or invalid link fails the commit.
    # NOTE: a DEFERRED trigger receives the row as it was when the event was queued (a stale NEW). The function therefore
    # re-reads the CURRENT row, so an earlier INSERT event is judged on the link that exists at COMMIT, not the one at insert.
    op.execute(
        """
        CREATE FUNCTION fin_txn_peer_integrity() RETURNS trigger AS $$
        DECLARE
            cur fin_transactions%ROWTYPE;
        BEGIN
            SELECT * INTO cur FROM fin_transactions WHERE id = NEW.id;
            IF NOT FOUND THEN
                RETURN NULL;  -- the row was deleted again before COMMIT: nothing to prove
            END IF;
            IF cur.transfer_peer_id IS NOT NULL THEN
                IF NOT EXISTS (
                    SELECT 1 FROM fin_transactions p
                    WHERE p.id = cur.transfer_peer_id
                      AND p.transfer_peer_id = cur.id
                      AND p.user_id = cur.user_id
                      AND p.account_id <> cur.account_id
                      AND p.direction <> cur.direction
                      AND p.amount = cur.amount
                ) THEN
                    RAISE EXCEPTION 'fin_transactions: a transfer link must be reciprocal, same user, different account, opposite direction and the same amount';
                END IF;
            END IF;
            IF EXISTS (
                SELECT 1 FROM fin_transactions q
                WHERE q.transfer_peer_id = cur.id AND cur.transfer_peer_id IS DISTINCT FROM q.id
            ) THEN
                RAISE EXCEPTION 'fin_transactions: another transaction still points at this one without being linked back';
            END IF;
            RETURN NULL;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE CONSTRAINT TRIGGER fin_txn_peer_integrity AFTER INSERT OR UPDATE OF "
        "transfer_peer_id, account_id, direction, amount ON fin_transactions "
        "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW EXECUTE FUNCTION fin_txn_peer_integrity()"
    )


def downgrade() -> None:
    t = "fin_transactions"
    op.execute("DROP TRIGGER IF EXISTS fin_txn_peer_integrity ON fin_transactions")
    op.execute("DROP FUNCTION IF EXISTS fin_txn_peer_integrity()")
    op.drop_index(
        "uq_fin_txn_transfer_peer",
        table_name=t,
        postgresql_where=sa.text("transfer_peer_id IS NOT NULL"),
    )
    for name in reversed(list(_CHECKS)):
        op.drop_constraint(op.f(f"ck_fin_transactions_{name}"), t, type_="check")
    op.drop_constraint(op.f("fk_fin_transactions_loan_id_fin_loans"), t, type_="foreignkey")
    op.drop_constraint(
        op.f("fk_fin_transactions_transfer_peer_id_fin_transactions"), t, type_="foreignkey"
    )
    for col in (
        "emi_interest",
        "emi_principal",
        "loan_id",
        "transfer_peer_id",
        "review_reason",
        "classification_confidence",
    ):
        op.drop_column(t, col)
