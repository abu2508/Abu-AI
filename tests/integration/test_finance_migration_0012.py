# mypy: disable-error-code="no-untyped-def,no-untyped-call,arg-type,type-arg,no-any-return,union-attr"
"""Migration 0012 on a REAL PostgreSQL: columns, constraints, the one-to-one index, the DEFERRED reciprocity trigger,
deletion semantics, and a working downgrade that leaves historical rows alone. Raw SQL on purpose: it bypasses the
service, so these prove what the DATABASE refuses by itself. Synthetic data only."""

import uuid
from collections.abc import Callable

import pytest
from alembic import command
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import DBAPIError, IntegrityError

from tests.integration.conftest import alembic_config

PEER_ERR = "reciprocal"


def mkuser(c, name: str = "u") -> uuid.UUID:
    return c.execute(
        text("INSERT INTO users (display_name) VALUES (:n) RETURNING id"), {"n": name}
    ).scalar_one()


def mkacct(c, uid, kind: str = "bank") -> uuid.UUID:
    return c.execute(
        text(
            "INSERT INTO fin_accounts (user_id, kind, label_enc) VALUES (:u, :k, '\\x00') RETURNING id"
        ),
        {"u": uid, "k": kind},
    ).scalar_one()


def mktxn(
    c,
    uid,
    acct,
    *,
    direction: str = "debit",
    amount: str = "100.00",
    kind: str = "transfer",
    status: str = "confirmed",
    **cols,
) -> uuid.UUID:
    """Insert one transaction. `cols` are extra columns (only valid after 0012)."""
    names = [
        "user_id",
        "account_id",
        "txn_date",
        "amount",
        "direction",
        "kind",
        "dedupe_key",
        "source",
        "status",
    ]
    vals = [":uid", ":acct", "date '2024-03-05'", ":amt", ":dir", ":kind", ":dk", "'manual'", ":st"]
    params = {
        "uid": uid,
        "acct": acct,
        "amt": amount,
        "dir": direction,
        "kind": kind,
        "dk": uuid.uuid4().hex,
        "st": status,
    }
    for k, v in cols.items():
        names.append(k)
        vals.append(f":{k}")
        params[k] = v
    sql = (
        f"INSERT INTO fin_transactions ({', '.join(names)}) VALUES ({', '.join(vals)}) RETURNING id"
    )
    return c.execute(text(sql), params).scalar_one()


def link(c, a, b) -> None:
    c.execute(
        text("UPDATE fin_transactions SET transfer_peer_id = :p WHERE id = :i"), {"p": b, "i": a}
    )
    c.execute(
        text("UPDATE fin_transactions SET transfer_peer_id = :p WHERE id = :i"), {"p": a, "i": b}
    )


def peers(c) -> dict:
    return {
        r[0]: r[1] for r in c.execute(text("SELECT id, transfer_peer_id FROM fin_transactions"))
    }


def assert_no_inconsistent_links(c) -> None:
    """The invariant the service and the database must both keep after ANY operation."""
    bad = c.execute(
        text(
            """
            SELECT a.id FROM fin_transactions a
            LEFT JOIN fin_transactions b ON b.id = a.transfer_peer_id
            WHERE a.transfer_peer_id IS NOT NULL
              AND (b.id IS NULL OR b.transfer_peer_id <> a.id OR a.status <> 'confirmed' OR a.kind <> 'transfer'
                   OR a.user_id <> b.user_id OR a.account_id = b.account_id OR a.direction = b.direction
                   OR a.amount <> b.amount)
            """
        )
    ).all()
    assert bad == []
    assert (
        c.execute(
            text(
                "SELECT count(*) FROM fin_transactions WHERE review_reason IS NOT NULL AND status <> 'pending_review'"
            )
        ).scalar_one()
        == 0
    )
    assert (
        c.execute(
            text(
                "SELECT count(*) FROM fin_transactions WHERE emi_principal IS NOT NULL AND (kind <> 'emi' OR direction <> 'debit')"
            )
        ).scalar_one()
        == 0
    )


@pytest.fixture
def db(migrated_url: URL):
    eng = create_engine(migrated_url)
    yield eng
    eng.dispose()


def pair(
    c,
    *,
    same_user: bool = True,
    same_account: bool = False,
    same_direction: bool = False,
    amounts=("100.00", "100.00"),
):
    u1 = mkuser(c, "a")
    u2 = u1 if same_user else mkuser(c, "b")
    a1 = mkacct(c, u1)
    a2 = a1 if same_account else mkacct(c, u2)
    t1 = mktxn(c, u1, a1, direction="debit", amount=amounts[0])
    t2 = mktxn(c, u2, a2, direction="debit" if same_direction else "credit", amount=amounts[1])
    return t1, t2


# ------------------------------------------------------------------------------------------------- the schema itself
def test_new_columns_are_nullable_and_the_orm_matches(db) -> None:
    with db.connect() as c:
        cols = {
            r[0]: r[1]
            for r in c.execute(
                text(
                    "SELECT column_name, is_nullable FROM information_schema.columns WHERE table_name = 'fin_transactions' "
                    "AND column_name IN ('classification_confidence','review_reason','transfer_peer_id','loan_id','emi_principal','emi_interest')"
                )
            )
        }
    assert cols == dict.fromkeys(
        [
            "classification_confidence",
            "review_reason",
            "transfer_peer_id",
            "loan_id",
            "emi_principal",
            "emi_interest",
        ],
        "YES",
    )


# ------------------------------------------------------------------------------------------------- reciprocal links
def test_a_valid_reciprocal_link_commits_in_any_update_order(db) -> None:
    with db.begin() as c:
        t1, t2 = pair(c)
        link(c, t1, t2)  # two statements inside ONE transaction: the check is deferred to COMMIT
    with db.connect() as c:
        assert peers(c) == {t1: t2, t2: t1}
        assert_no_inconsistent_links(c)


@pytest.mark.parametrize(
    "case",
    ["one_sided", "other_user", "same_account", "same_direction", "unequal_amount"],
)
def test_invalid_or_one_sided_links_fail_at_commit(db, case: str) -> None:
    with db.connect() as c0:
        c0.execute(text("SELECT 1"))
    with pytest.raises(DBAPIError, match=PEER_ERR + "|still points"), db.begin() as c:
        kw = {
            "one_sided": {},
            "other_user": {"same_user": False},
            "same_account": {"same_account": True},
            "same_direction": {"same_direction": True},
            "unequal_amount": {"amounts": ("100.00", "100.01")},
        }[case]
        t1, t2 = pair(c, **kw)
        if case == "one_sided":
            c.execute(
                text("UPDATE fin_transactions SET transfer_peer_id = :p WHERE id = :i"),
                {"p": t2, "i": t1},
            )
        else:
            link(c, t1, t2)
    with db.connect() as c:  # nothing was persisted
        assert c.execute(text("SELECT count(*) FROM fin_transactions")).scalar_one() == 0


def test_a_self_link_and_a_link_on_a_non_confirmed_or_non_transfer_row_are_refused_immediately(
    db,
) -> None:
    with db.begin() as c:
        t1, t2 = pair(c)
    with pytest.raises(IntegrityError), db.begin() as c:
        c.execute(
            text("UPDATE fin_transactions SET transfer_peer_id = id WHERE id = :i"), {"i": t1}
        )
    for col, val in (("kind", "expense"), ("status", "pending_review")):
        with pytest.raises(IntegrityError), db.begin() as c:
            c.execute(
                text(
                    f"UPDATE fin_transactions SET {col} = :v, transfer_peer_id = :p WHERE id = :i"
                ),
                {"v": val, "p": t2, "i": t1},
            )


def test_two_rows_cannot_point_at_the_same_peer(db) -> None:
    with db.begin() as c:
        u = mkuser(c)
        a1, a2, a3 = mkacct(c, u), mkacct(c, u), mkacct(c, u)
        target = mktxn(c, u, a1, direction="debit")
        x = mktxn(c, u, a2, direction="credit")
        y = mktxn(c, u, a3, direction="credit")
        link(c, target, x)
    with pytest.raises(DBAPIError), db.begin() as c:  # y tries to claim the already-linked target
        c.execute(
            text("UPDATE fin_transactions SET transfer_peer_id = :p WHERE id = :i"),
            {"p": target, "i": y},
        )
    with db.connect() as c:
        assert_no_inconsistent_links(c)


def test_unlinking_one_side_only_is_refused_and_both_sides_together_is_allowed(db) -> None:
    with db.begin() as c:
        t1, t2 = pair(c)
        link(c, t1, t2)
    with pytest.raises(DBAPIError, match="still points"), db.begin() as c:
        c.execute(
            text("UPDATE fin_transactions SET transfer_peer_id = NULL WHERE id = :i"), {"i": t1}
        )
    with db.begin() as c:
        c.execute(text("UPDATE fin_transactions SET transfer_peer_id = NULL"))
    with db.connect() as c:
        assert set(peers(c).values()) == {None}


def test_deleting_one_leg_keeps_the_other_and_clears_its_peer(db) -> None:
    with db.begin() as c:
        t1, t2 = pair(c)
        link(c, t1, t2)
    with db.begin() as c:
        c.execute(text("DELETE FROM fin_transactions WHERE id = :i"), {"i": t1})
    with db.connect() as c:
        assert peers(c) == {t2: None}  # ON DELETE SET NULL: the survivor stays and points at nobody
        assert_no_inconsistent_links(c)


# ------------------------------------------------------------------------------------------------- review and confidence
def test_review_reason_rules(db) -> None:
    with db.begin() as c:
        u = mkuser(c)
        a = mkacct(c, u)
        mktxn(
            c,
            u,
            a,
            kind="income",
            status="pending_review",
            review_reason="unknown_credit",
            classification_confidence="0.00",
        )
    for kw in (
        {"status": "confirmed", "review_reason": "unknown_credit"},  # a reason only while pending
        {"status": "pending_review", "review_reason": "because"},  # an unsupported reason
        {"status": "confirmed", "classification_confidence": "1.50"},  # out of range
        {"status": "confirmed", "classification_confidence": "-0.10"},
    ):
        with pytest.raises(IntegrityError), db.begin() as c:
            u = mkuser(c)
            a = mkacct(c, u)
            mktxn(c, u, a, kind="income", **kw)


def test_confidence_boundaries_are_accepted(db) -> None:
    with db.begin() as c:
        u = mkuser(c)
        a = mkacct(c, u)
        for v in ("0.00", "0.50", "1.00"):
            mktxn(c, u, a, kind="income", classification_confidence=v)


# ------------------------------------------------------------------------------------------------- EMI split constraints
def emi(c, **cols):
    u = mkuser(c)
    a = mkacct(c, u)
    return mktxn(
        c,
        u,
        a,
        kind=cols.pop("kind", "emi"),
        direction=cols.pop("direction", "debit"),
        amount="8884.88",
        **cols,
    )


def test_emi_split_constraints(db) -> None:
    with db.begin() as c:
        emi(c)  # no split at all: fine (legacy and unverified rows)
        emi(c, emi_principal="7884.88", emi_interest="1000.00")  # valid split
        emi(c, emi_principal="8884.88", emi_interest="0.00")  # zero interest is valid
    bad = [
        {"emi_principal": "7884.88"},  # only one part
        {"emi_interest": "1000.00"},
        {"emi_principal": "-1.00", "emi_interest": "8885.88"},  # negative
        {"emi_principal": "8884.89", "emi_interest": "-0.01"},
        {"emi_principal": "7884.88", "emi_interest": "1000.01"},  # sum differs from the amount
        {"emi_principal": "7884.88", "emi_interest": "1000.00", "kind": "expense"},  # not an EMI
        {
            "emi_principal": "7884.88",
            "emi_interest": "1000.00",
            "direction": "credit",
        },  # not a debit
    ]
    for kw in bad:
        with pytest.raises(IntegrityError), db.begin() as c:
            emi(c, **kw)


def test_a_loan_link_needs_a_split_and_a_deleted_loan_keeps_the_split(db) -> None:
    with db.begin() as c:
        u = mkuser(c)
        a = mkacct(c, u)
        loan = c.execute(
            text(
                "INSERT INTO fin_loans (user_id, label_enc, principal, annual_rate_pct, tenor_months, start_date, emi_amount) "
                "VALUES (:u, '\\x00', 100000, 12, 12, date '2024-01-05', 8884.88) RETURNING id"
            ),
            {"u": u},
        ).scalar_one()
        t = mktxn(
            c,
            u,
            a,
            kind="emi",
            amount="8884.88",
            loan_id=loan,
            emi_principal="7884.88",
            emi_interest="1000.00",
        )
    with pytest.raises(IntegrityError), db.begin() as c:  # loan_id without a split
        mktxn(c, u, a, kind="emi", loan_id=loan)
    with db.begin() as c:
        c.execute(text("DELETE FROM fin_loans WHERE id = :i"), {"i": loan})
    with db.connect() as c:
        row = c.execute(
            text("SELECT loan_id, emi_principal, emi_interest FROM fin_transactions WHERE id = :i"),
            {"i": t},
        ).one()
    assert row[0] is None and (str(row[1]), str(row[2])) == ("7884.88", "1000.00")


# ------------------------------------------------------------------------------------------------- upgrade and downgrade
def test_downgrade_removes_everything_and_leaves_historical_rows_untouched(
    scratch_db: Callable[[], URL],
) -> None:
    url = scratch_db()
    cfg = alembic_config(url)
    command.upgrade(cfg, "0011")
    eng = create_engine(url)
    with eng.begin() as c:  # a HISTORICAL row, created before 0012 exists
        u = mkuser(c)
        a = mkacct(c, u)
        legacy = mktxn(c, u, a, kind="income", direction="credit", status="confirmed")
    command.upgrade(cfg, "head")
    with eng.connect() as c:
        row = c.execute(
            text(
                "SELECT classification_confidence, review_reason, transfer_peer_id, loan_id, emi_principal, emi_interest, "
                "status, kind FROM fin_transactions WHERE id = :i"
            ),
            {"i": legacy},
        ).one()
        assert row[:6] == (None,) * 6 and row[6:] == (
            "confirmed",
            "income",
        )  # NO backfill, nothing rewritten
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'fin_txn_peer_integrity'")
            ).scalar_one()
            == 1
        )
    command.downgrade(cfg, "0011")
    with eng.connect() as c:
        cols = {
            r[0]
            for r in c.execute(
                text(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = 'fin_transactions'"
                )
            )
        }
        assert not cols & {
            "classification_confidence",
            "review_reason",
            "transfer_peer_id",
            "loan_id",
            "emi_principal",
            "emi_interest",
        }
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_trigger WHERE tgname = 'fin_txn_peer_integrity'")
            ).scalar_one()
            == 0
        )
        assert (
            c.execute(
                text("SELECT count(*) FROM pg_proc WHERE proname = 'fin_txn_peer_integrity'")
            ).scalar_one()
            == 0
        )
        assert c.execute(
            text("SELECT status, kind FROM fin_transactions WHERE id = :i"), {"i": legacy}
        ).one() == ("confirmed", "income")
        assert (
            c.execute(
                text(
                    "SELECT count(*) FROM pg_constraint WHERE conname LIKE 'ck_fin_transactions_%' AND conname IN ('ck_fin_transactions_emi_split_sum','ck_fin_transactions_transfer_peer_not_self')"
                )
            ).scalar_one()
            == 0
        )
    command.upgrade(cfg, "head")  # and up again
    eng.dispose()
