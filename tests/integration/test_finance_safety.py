# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,type-arg,attr-defined,assignment,operator"
"""S2.1A finance classification safety against a real database. Synthetic data only.

Invariants: unknown credits wait for review and never count as income; a card purchase plus its bill payment is ONE
expense; an own-account transfer needs evidence on BOTH legs (or an explicit user assertion) and is linked atomically and
reciprocally; an established confirmed row is never reclassified from amount/date/direction alone; an unsplit confirmed
EMI is cash outflow but not recognised expense; historical rows keep their behaviour; INR only.
"""

import base64
import uuid
from datetime import date
from decimal import Decimal as D

import pytest
from sqlalchemy import text

from database.schemas.finance import FinTransaction
from domain.finance.analytics import add_months
from domain.finance.service import FinanceService
from tests.integration.conftest import Api
from tests.integration.helpers import Device
from tests.integration.test_finance import M, Y, bank, d, dev2, dmy, get, post, txn

NEW_KEYS = {
    "classification_confidence",
    "review_reason",
    "transfer_peer_id",
    "loan_id",
    "emi_principal",
    "emi_interest",
}


# ------------------------------------------------------------------------------------------------------ helpers
async def card(api: Api, dev: Device, last4: str = "5678") -> str:
    body = {"kind": "credit_card", "label": "Card", "last4": last4, "credit_limit": "10000"}
    return (await post(api, dev, "/accounts", body, 201)).json()["id"]


async def add(
    api: Api, dev: Device, acct: str, day: int, amount: str, direction: str, desc: str, **kw
):
    r = (
        await post(api, dev, "/transactions", txn(acct, day, amount, direction, desc, **kw), 201)
    ).json()
    return r["transaction_id"], r


async def row(api: Api, dev: Device, tid: str) -> dict:
    rows = await get(api, dev, "/transactions", limit=500)
    return next(t for t in rows if t["id"] == tid)


async def sql(api: Api, stmt: str, **params):
    async with api.sessions() as s:
        res = await s.execute(text(stmt), params)
        await s.commit()
        return res


async def links_ok(api: Api) -> None:
    """The invariant that must hold after ANY operation: no inconsistent links remain."""
    async with api.sessions() as s:
        bad = (
            await s.execute(
                text(
                    """SELECT a.id FROM fin_transactions a LEFT JOIN fin_transactions b ON b.id = a.transfer_peer_id
                       WHERE a.transfer_peer_id IS NOT NULL AND (b.id IS NULL OR b.transfer_peer_id <> a.id
                         OR a.status <> 'confirmed' OR a.kind <> 'transfer' OR a.user_id <> b.user_id
                         OR a.account_id = b.account_id OR a.direction = b.direction OR a.amount <> b.amount)"""
                )
            )
        ).all()
        reasons = (
            await s.execute(
                text(
                    "SELECT count(*) FROM fin_transactions WHERE review_reason IS NOT NULL AND status <> 'pending_review'"
                )
            )
        ).scalar_one()
        splits = (
            await s.execute(
                text(
                    "SELECT count(*) FROM fin_transactions WHERE emi_principal IS NOT NULL AND kind <> 'emi'"
                )
            )
        ).scalar_one()
    assert bad == [] and reasons == 0 and splits == 0


async def summary(api: Api, dev: Device) -> dict:
    return await get(api, dev, "/summary", year=Y, month=M)


def csv_b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


async def legacy_row(api: Api, dev: Device, acct: str, **kw) -> str:
    """A HISTORICAL row: created without any S2.1A column (classification_confidence stays NULL)."""
    p = {
        "kind": "income",
        "direction": "credit",
        "status": "confirmed",
        "category": "other_income",
        "amount": "300.00",
        "day": d(10),
        **kw,
    }
    res = await sql(
        api,
        "INSERT INTO fin_transactions (user_id, account_id, txn_date, amount, direction, category, kind, dedupe_key, source, status) "
        "VALUES (:u, :a, CAST(:day AS date), :amount, :direction, :category, :kind, :dk, 'manual', :status) RETURNING id",
        u=dev.user_id,
        a=uuid.UUID(acct),
        dk=uuid.uuid4().hex,
        **p,
    )
    return str(res.scalar_one())


# ------------------------------------------------------------------------------------------------------ FIN-09: confidence and routing
async def test_unknown_credits_wait_for_review_and_never_count_as_income(
    api: Api, device: Device
) -> None:
    acct = await bank(api, device)
    tid, out = await add(api, device, acct, 3, "500.00", "credit", "UPI/222222/RAJESH K")
    assert out["transaction_status"] == "pending_review"
    r = await row(api, device, tid)
    assert (r["review_reason"], D(r["classification_confidence"]), r["category"]) == (
        "unknown_credit",
        D("0.00"),
        "other_income",
    )
    assert set(r) >= NEW_KEYS
    s = await summary(api, device)
    assert D(s["income"]) == 0 and s["pending_review_count"] == 1
    # an unknown DEBIT is not routed (policy covers credits): it still counts as 'uncategorized' expense
    await add(api, device, acct, 4, "250.00", "debit", "mystery")
    assert D((await summary(api, device))["expense"]) == D("250.00")
    # a KNOWN credit is not held
    _, salary = await add(api, device, acct, 1, "50000.00", "credit", "ACME SALARY")
    assert salary["transaction_status"] == "confirmed"
    assert D((await summary(api, device))["income"]) == D("50000.00")
    await links_ok(api)


async def test_unknown_credits_stay_pending_on_auto_confirm_accounts_and_in_statements(
    api: Api, device: Device
) -> None:
    acct = await bank(api, device)
    await api.client.patch(
        f"/v1/finance/accounts/{acct}", json={"auto_confirm": True}, headers=device.headers
    )
    sms = f"Rs.900.00 credited to A/c XX1234 on {dmy(6)} by RAJESH KUMAR. Ref 998877661"
    out = (await post(api, device, "/ingest", {"channel": "sms", "text": sms})).json()
    assert (
        out["transaction_status"] == "pending_review"
    )  # auto_confirm does NOT release an unknown credit
    csv = (
        b"Date,Narration,Withdrawal Amt,Deposit Amt\n"
        + f"{dmy(7)},UPI/333333/SOMEONE,,700.00\n{dmy(8)},ACME SALARY,,5000.00\n".encode()
    )
    imp = (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "csv", "content_base64": csv_b64(csv)},
        )
    ).json()
    assert imp["imported"] == 2
    s = await summary(api, device)
    assert (
        D(s["income"]) == D("5000.00") and s["pending_review_count"] == 2
    )  # only the salary counts


async def test_confirming_an_unknown_credit_needs_an_explicit_category(
    api: Api, device: Device
) -> None:
    acct = await bank(api, device)
    tid, _ = await add(api, device, acct, 3, "500.00", "credit", "UPI/222222/RAJESH K")
    bad = await post(api, device, f"/transactions/{tid}/confirm", {}, None)
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "category_required"
    assert D((await summary(api, device))["income"]) == 0
    ok = (
        await post(api, device, f"/transactions/{tid}/confirm", {"category": "other_income"})
    ).json()
    assert (
        ok["status"] == "confirmed"
        and ok["review_reason"] is None
        and D(ok["classification_confidence"]) == D("1.00")
    )
    assert D((await summary(api, device))["income"]) == D("500.00")


async def test_user_recategorisation_sets_full_confidence(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    tid, _ = await add(api, device, acct, 3, "80.00", "debit", "SWIGGY")
    assert D((await row(api, device, tid))["classification_confidence"]) == D("0.80")
    r = (await post(api, device, f"/transactions/{tid}/category", {"category": "groceries"})).json()
    assert D(r["classification_confidence"]) == D("1.00") and r["category"] == "groceries"


async def test_bulk_confirm_skips_rows_with_a_review_reason(api: Api, device: Device) -> None:
    await bank(api, device)
    debit = f"Rs.80.00 debited from A/c XX1234 on {dmy(6)} at KIOSK Ref 99887755"
    credit = f"Rs.90.00 credited to A/c XX1234 on {dmy(6)} by RAJESH. Ref 99887766"
    await post(api, device, "/ingest", {"channel": "sms", "text": debit})
    await post(api, device, "/ingest", {"channel": "sms", "text": credit})
    assert (await post(api, device, "/transactions/confirm-pending", None)).json() == {
        "confirmed": 1
    }
    pend = (await get(api, device, "/review"))["pending_review"]
    assert [p["review_reason"] for p in pend] == ["unknown_credit"]


# ------------------------------------------------------------------------------------------------------ legacy rows
async def test_legacy_rows_keep_their_behaviour(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    # a confirmed legacy 'other_income' credit still counts as income (no retroactive rerouting)
    await legacy_row(api, device, acct)
    assert D((await summary(api, device))["income"]) == D("300.00")
    # a legacy PENDING row is bulk-confirmable exactly as before, and nothing else about it changes
    pend = await legacy_row(api, device, acct, status="pending_review", amount="40.00", day=d(11))
    assert (await post(api, device, "/transactions/confirm-pending", None)).json() == {
        "confirmed": 1
    }
    r = await row(api, device, pend)
    assert (
        r["status"],
        r["classification_confidence"],
        r["review_reason"],
        r["transfer_peer_id"],
    ) == ("confirmed", None, None, None)
    assert (r["category"], r["kind"]) == ("other_income", "income")
    # a legacy confirmed EMI still counts in full; a legacy transfer is never auto-linked
    await legacy_row(
        api,
        device,
        acct,
        kind="emi",
        direction="debit",
        category="emi_loans",
        amount="8884.88",
        day=d(12),
    )
    assert D((await summary(api, device))["expense"]) == D("8884.88")
    other = await bank(api, device, last4="9999")
    old = await legacy_row(
        api,
        device,
        acct,
        kind="transfer",
        direction="debit",
        category="transfers",
        amount="777.00",
        day=d(14),
    )
    _, out = await add(api, device, other, 14, "777.00", "credit", "SELF TRANSFER FROM SAVINGS")
    assert out["transaction_status"] == "pending_review"
    assert (await row(api, device, old))["transfer_peer_id"] is None
    await links_ok(api)


# ------------------------------------------------------------------------------------------------------ FIN-01 / FIN-03
@pytest.mark.parametrize("bank_first", [True, False])
async def test_card_purchase_and_bill_payment_are_one_expense(
    api: Api, device: Device, bank_first: bool
) -> None:
    b, c = await bank(api, device), await card(api, device)
    await add(api, device, c, 5, "1000.00", "debit", "AMAZON PURCHASE")
    legs = [
        (b, "debit", "CC PAYMENT HDFC CARD XX5678"),
        (c, "credit", "PAYMENT RECEIVED THANK YOU"),
    ]
    ids = []
    for acct, direction, desc in legs if bank_first else legs[::-1]:
        tid, _ = await add(api, device, acct, 20, "1000.00", direction, desc)
        ids.append(tid)
        if (
            len(ids) == 1
        ):  # before the second leg exists: the payment leg is held, never counted as expense/income
            first = await row(api, device, tid)
            assert (first["status"], first["review_reason"]) == (
                "pending_review",
                "unlinked_transfer",
            )
            s = await summary(api, device)
            assert (D(s["expense"]), D(s["income"])) == (D("1000.00"), D("0.00"))
    a, bb = await row(api, device, ids[0]), await row(api, device, ids[1])
    assert (a["status"], bb["status"]) == ("confirmed", "confirmed")
    assert (a["transfer_peer_id"], bb["transfer_peer_id"]) == (ids[1], ids[0])  # reciprocal
    assert (a["kind"], bb["kind"]) == ("transfer", "transfer")
    s = await summary(api, device)
    assert (D(s["expense"]), D(s["income"])) == (
        D("1000.00"),
        D("0.00"),
    )  # ONE expense, no phantom income
    cards = await get(api, device, "/cards")
    assert D(cards[0]["owed"]) == D("0.00")  # the payment reduced what is owed
    await links_ok(api)


@pytest.mark.parametrize("credit_first", [True, False])
async def test_own_account_transfer_in_both_directions_is_neither_income_nor_expense(
    api: Api, device: Device, credit_first: bool
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    legs = [(a, "debit", "NEFT TO SELF HDFC SAVINGS"), (b, "credit", "SELF TRANSFER FROM CURRENT")]
    ids = [
        (await add(api, device, acct, 5, "5000.00", direction, desc))[0]
        for acct, direction, desc in (legs[::-1] if credit_first else legs)
    ]
    r = [await row(api, device, i) for i in ids]
    assert [x["status"] for x in r] == ["confirmed", "confirmed"]
    s = await summary(api, device)
    assert (D(s["income"]), D(s["expense"])) == (D(0), D(0))
    await links_ok(api)


async def test_rent_transfer_stays_rent_and_ordinary_incoming_payments_are_not_transfers(
    api: Api, device: Device
) -> None:
    a = await bank(api, device)
    tid, out = await add(api, device, a, 3, "18000.00", "debit", "RENT TRANSFER")
    r = await row(api, device, tid)
    assert (r["category"], r["kind"], r["status"]) == ("rent", "expense", "confirmed")
    cid, _ = await add(api, device, a, 4, "18000.00", "credit", "PAYMENT RECEIVED FROM CLIENT")
    c = await row(api, device, cid)
    assert (c["kind"], c["status"], c["review_reason"], c["transfer_peer_id"]) == (
        "income",
        "pending_review",
        "unknown_credit",
        None,
    )


# ----------------------------------------------------------------------- transfer matching safeguards: nothing links unless ALL hold
async def held(api, dev, tid, reason="unlinked_transfer") -> None:
    r = await row(api, dev, tid)
    assert (r["status"], r["review_reason"], r["transfer_peer_id"]) == (
        "pending_review",
        reason,
        None,
    ), r


async def test_matching_window_is_three_days(api: Api, device: Device) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    d1, _ = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF")
    c3, _ = await add(
        api, device, b, 8, "100.00", "credit", "SELF TRANSFER FROM X"
    )  # exactly 3 days: links
    assert (await row(api, device, c3))["transfer_peer_id"] == d1
    d2, _ = await add(api, device, a, 12, "200.00", "debit", "NEFT TO SELF")
    c4, _ = await add(
        api, device, b, 16, "200.00", "credit", "SELF TRANSFER FROM X"
    )  # 4 days: does not
    await held(api, device, d2)
    await held(api, device, c4)
    await links_ok(api)


@pytest.mark.parametrize(
    "case",
    ["same_account", "same_direction", "amount_differs", "other_user", "no_evidence_on_candidate"],
)
async def test_nothing_links_unless_every_safeguard_holds(
    api: Api, device: Device, case: str
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    first, _ = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF")
    if case == "same_account":
        second, _ = await add(api, device, a, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    elif case == "same_direction":
        second, _ = await add(api, device, b, 6, "100.00", "debit", "NEFT TO SELF")
    elif case == "amount_differs":
        second, _ = await add(api, device, b, 6, "100.01", "credit", "SELF TRANSFER FROM X")
    elif case == "other_user":
        dev_b = await dev2(api)
        ob = await bank(api, dev_b)
        second, _ = await add(api, dev_b, ob, 6, "100.00", "credit", "SELF TRANSFER FROM X")
        await held(api, dev_b, second)
        await held(api, device, first)
        await links_ok(api)
        return
    else:  # the other leg carries NO transfer evidence of its own (an unknown credit): amount/date/direction are not enough
        second, _ = await add(api, device, b, 6, "100.00", "credit", "UPI/222222/RAJESH K")
        assert (await row(api, device, second))["review_reason"] == "unknown_credit"
        await held(api, device, first)
        await links_ok(api)
        return
    await held(api, device, first)
    await held(api, device, second)
    await links_ok(api)


async def test_an_established_confirmed_row_is_never_reclassified_by_amount_date_and_direction(
    api: Api, device: Device
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    exp, _ = await add(
        api, device, a, 5, "5000.00", "debit", "UPI/444444/LANDLORD"
    )  # confirmed 'uncategorized' expense
    cr, _ = await add(api, device, b, 6, "5000.00", "credit", "SELF TRANSFER FROM SAVINGS")
    r = await row(api, device, exp)
    assert (r["status"], r["kind"], r["transfer_peer_id"]) == (
        "confirmed",
        "expense",
        None,
    )  # untouched
    await held(api, device, cr)
    # ONLY an explicit user decision re-classifies it, and that decision then links the pair
    done = (
        await post(api, device, f"/transactions/{exp}/category", {"category": "transfers"})
    ).json()
    assert (done["kind"], D(done["classification_confidence"])) == ("transfer", D("1.00"))
    a_, b_ = await row(api, device, exp), await row(api, device, cr)
    assert (a_["transfer_peer_id"], b_["transfer_peer_id"]) == (cr, exp)
    assert (a_["status"], b_["status"]) == ("confirmed", "confirmed")
    await links_ok(api)


async def test_ambiguous_matches_stay_under_review(api: Api, device: Device) -> None:
    a1, a2, b = (
        await bank(api, device),
        await bank(api, device, last4="8888"),
        await bank(api, device, last4="9999"),
    )
    d1, _ = await add(api, device, a1, 5, "100.00", "debit", "NEFT TO SELF")
    d2, _ = await add(api, device, a2, 5, "100.00", "debit", "NEFT TO SELF")
    cr, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    await held(api, device, cr, "ambiguous_transfer")
    await held(api, device, d1)  # candidates untouched
    await held(api, device, d2)
    await links_ok(api)


async def test_an_apparently_unique_candidate_is_not_linked_when_its_own_partners_are_ambiguous(
    api: Api, device: Device
) -> None:
    """MUTUAL uniqueness. The new credit sees exactly ONE candidate debit, but that debit has several eligible credits
    of its own, so pairing the new credit with it would be a guess. Nothing may link; every leg stays reviewable."""
    a, b, b2, b3 = (
        await bank(api, device),
        await bank(api, device, last4="9999"),
        await bank(api, device, last4="7777"),
        await bank(api, device, last4="6666"),
    )
    c2, _ = await add(api, device, b2, 4, "100.00", "credit", "SELF TRANSFER FROM X")
    c3, _ = await add(api, device, b3, 4, "100.00", "credit", "SELF TRANSFER FROM Y")
    d1, _ = await add(
        api, device, a, 5, "100.00", "debit", "NEFT TO SELF"
    )  # two credits fit it: ambiguous
    await held(api, device, d1, "ambiguous_transfer")
    # the new credit: exactly one candidate (d1) exists for it, and d1 is within its window
    c, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM Z")
    async with api.sessions() as s:
        svc = FinanceService(s, None)
        credit = await s.get(FinTransaction, uuid.UUID(c))
        assert [x.id for x in await svc._partners(device.user_id, credit)] == [uuid.UUID(d1)]
        await s.rollback()
    await held(api, device, c, "ambiguous_transfer")  # not linked, marked for review
    await held(api, device, d1, "ambiguous_transfer")
    await held(api, device, c2)  # the other legs are untouched and still reviewable
    await held(api, device, c3)
    await links_ok(api)
    # every leg is still an ordinary pending row the owner can resolve explicitly
    got = {t["id"]: t for t in await get(api, device, "/transactions", limit=500)}
    assert all(got[i]["transfer_peer_id"] is None for i in (c, c2, c3, d1))
    assert all(got[i]["status"] == "pending_review" for i in (c, c2, c3, d1))


async def test_duplicate_suspects_are_never_link_candidates(api: Api, device: Device) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    d1, _ = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF HDFC")
    dup, out = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF HDFC SAVINGS")
    assert out["transaction_status"] == "duplicate_suspect"
    cr, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    assert (await row(api, device, cr))["transfer_peer_id"] == d1  # only the genuine candidate
    suspect = await row(api, device, dup)
    assert (suspect["status"], suspect["transfer_peer_id"]) == ("duplicate_suspect", None)
    await links_ok(api)


async def test_matching_is_idempotent(api: Api, device: Device) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    sms = (
        f"Rs.100.00 debited from A/c XX1234 on {dmy(5)} to SELF TRANSFER OWN ACCOUNT. Ref 123123123"
    )
    await post(api, device, "/ingest", {"channel": "sms", "text": sms})
    cr, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    before = await get(api, device, "/transactions", limit=500)
    again = (await post(api, device, "/ingest", {"channel": "sms", "text": sms})).json()
    assert again["status"] == "duplicate"  # a retry returns before any matching
    assert await get(api, device, "/transactions", limit=500) == before
    await links_ok(api)
    assert a and cr


async def test_a_failure_in_the_middle_of_linking_leaves_nothing_behind(
    api: Api, device: Device, monkeypatch: pytest.MonkeyPatch
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    d1, _ = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF")

    async def boom(self, x, y):
        x.transfer_peer_id = y.id
        x.kind, x.status, x.review_reason = "transfer", "confirmed", None
        await self._s.flush()  # half of the link is written ...
        raise RuntimeError("boom")  # ... then the request fails

    monkeypatch.setattr(FinanceService, "_link_pair", boom)
    with pytest.raises(RuntimeError):
        await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    monkeypatch.undo()
    rows = await get(api, device, "/transactions", limit=500)
    assert [r["id"] for r in rows] == [d1]  # the new leg was rolled back with the failed link
    await held(api, device, d1)
    await links_ok(api)


# ------------------------------------------------------------------------------------------------------ link lifecycle
async def linked_pair(api: Api, dev: Device) -> tuple[str, str]:
    a, b = await bank(api, dev), await bank(api, dev, last4="9999")
    d1, _ = await add(api, dev, a, 5, "100.00", "debit", "NEFT TO SELF")
    c1, _ = await add(api, dev, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    assert (await row(api, dev, d1))["transfer_peer_id"] == c1
    return d1, c1


async def test_deleting_one_leg_demotes_an_auto_confirmed_survivor(
    api: Api, device: Device
) -> None:
    d1, c1 = await linked_pair(api, device)
    assert (
        await api.client.delete(f"/v1/finance/transactions/{d1}", headers=device.headers)
    ).status_code == 204
    await held(api, device, c1)  # back to review: its evidence was the other leg
    assert D((await summary(api, device))["income"]) == 0
    await links_ok(api)


async def test_deleting_a_leg_keeps_a_user_asserted_survivor_confirmed(
    api: Api, device: Device
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    asserted, _ = await add(
        api, device, a, 5, "100.00", "debit", "UPI/555555/MY OTHER BANK", category="transfers"
    )
    assert D((await row(api, device, asserted))["classification_confidence"]) == D("1.00")
    ev, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    assert (await row(api, device, ev))["transfer_peer_id"] == asserted
    await api.client.delete(f"/v1/finance/transactions/{ev}", headers=device.headers)
    r = await row(api, device, asserted)
    assert (r["status"], r["kind"], r["transfer_peer_id"]) == (
        "confirmed",
        "transfer",
        None,
    )  # the user's own decision stands
    await links_ok(api)


async def test_rejecting_a_linked_leg_unlinks_both_and_demotes_the_survivor(
    api: Api, device: Device
) -> None:
    d1, c1 = await linked_pair(api, device)
    assert (await post(api, device, f"/transactions/{d1}/reject", None)).json()[
        "status"
    ] == "rejected"
    assert (await row(api, device, d1))["transfer_peer_id"] is None
    await held(api, device, c1)
    await links_ok(api)


async def test_recategorising_a_linked_leg_away_from_transfers_unlinks_both(
    api: Api, device: Device
) -> None:
    d1, c1 = await linked_pair(api, device)
    r = (await post(api, device, f"/transactions/{d1}/category", {"category": "groceries"})).json()
    assert (r["kind"], r["transfer_peer_id"], r["status"]) == ("expense", None, "confirmed")
    assert D(r["classification_confidence"]) == D("1.00")
    await held(api, device, c1)
    await links_ok(api)


async def test_confirming_an_unlinked_transfer_is_a_user_assertion_that_later_links(
    api: Api, device: Device
) -> None:
    a, b = await bank(api, device), await bank(api, device, last4="9999")
    d1, _ = await add(api, device, a, 5, "100.00", "debit", "NEFT TO SELF")
    r = (await post(api, device, f"/transactions/{d1}/confirm", {})).json()
    assert (r["status"], r["kind"], r["transfer_peer_id"], D(r["classification_confidence"])) == (
        "confirmed",
        "transfer",
        None,
        D("1.00"),
    )
    c1, _ = await add(api, device, b, 6, "100.00", "credit", "SELF TRANSFER FROM X")
    assert (await row(api, device, c1))["transfer_peer_id"] == d1
    await links_ok(api)


# ------------------------------------------------------------------------------------------------------ FIN-02 statements and INR
async def test_ambiguous_card_statements_are_rejected_whole(api: Api, device: Device) -> None:
    c = await card(api, device)
    csv = f"Date,Description,Amount\n{dmy(5)},AMAZON,1000.00\n{dmy(20)},PAYMENT RECEIVED,-1000.00\n".encode()
    r = await post(
        api,
        device,
        "/import",
        {"account_id": c, "format": "csv", "content_base64": csv_b64(csv)},
        None,
    )
    assert r.status_code == 422 and r.json()["error"]["code"] == "ambiguous_card_amount_sign"
    assert await get(api, device, "/transactions") == []  # nothing imported


async def test_a_card_statement_with_explicit_columns_imports_and_links_the_payment(
    api: Api, device: Device
) -> None:
    b, c = await bank(api, device), await card(api, device)
    bank_leg, _ = await add(api, device, b, 20, "1000.00", "debit", "CC PAYMENT HDFC CARD XX5678")
    csv = (
        f"Date,Description,Debit,Credit\n{dmy(5)},AMAZON PURCHASE,1000.00,\n{dmy(20)},PAYMENT RECEIVED THANK YOU,,1000.00\n"
    ).encode()
    imp = (
        await post(
            api,
            device,
            "/import",
            {"account_id": c, "format": "csv", "content_base64": csv_b64(csv)},
        )
    ).json()
    assert imp["imported"] == 2
    assert (await row(api, device, bank_leg))["status"] == "confirmed"
    s = await summary(api, device)
    assert (D(s["expense"]), D(s["income"])) == (D("1000.00"), D("0.00"))
    await links_ok(api)


async def test_dr_cr_suffix_decides_direction_on_a_bank_statement(api: Api, device: Device) -> None:
    b = await bank(api, device)
    csv = f"Date,Description,Amount\n{dmy(5)},COFFEE,120.00 Dr\n{dmy(6)},ACME SALARY,5000.00 Cr\n".encode()
    imp = (
        await post(
            api,
            device,
            "/import",
            {"account_id": b, "format": "csv", "content_base64": csv_b64(csv)},
        )
    ).json()
    assert imp["imported"] == 2
    s = await summary(api, device)
    assert (D(s["expense"]), D(s["income"])) == (D("120.00"), D("5000.00"))


async def test_other_currencies_are_rejected_everywhere(api: Api, device: Device) -> None:
    b = await bank(api, device)
    csv = f"Date,Description,Debit,Credit,Currency\n{dmy(5)},HOTEL,50.00,,USD\n{dmy(6)},COFFEE,10.00,,INR\n".encode()
    imp = (
        await post(
            api,
            device,
            "/import",
            {"account_id": b, "format": "csv", "content_base64": csv_b64(csv)},
        )
    ).json()
    assert (imp["imported"], imp["rejected"], imp["reject_reasons"]) == (
        1,
        1,
        {"unsupported_currency": 1},
    )
    v = await post(
        api,
        device,
        "/transactions/voice",
        {"account_id": b, "transcript": "spent 50 dollars on coffee"},
        None,
    )
    assert v.status_code == 422 and v.json()["error"]["code"] == "unsupported_currency"
    n = (
        await post(
            api,
            device,
            "/ingest",
            {"channel": "sms", "text": f"USD 5.00 debited from A/c XX1234 on {dmy(5)} at SHOP"},
        )
    ).json()
    assert n["status"] == "unparsed"


# ------------------------------------------------------------------------------------------------------ EMI
EMI = D("8884.88")


async def loan(api: Api, dev: Device, label: str = "Car loan") -> str:
    start = add_months(
        date(Y, M, 5), -1
    )  # the first instalment falls due on the 5th of the test month
    body = {
        "label": label,
        "principal": "100000",
        "annual_rate_pct": "12",
        "tenor_months": 12,
        "start_date": start.isoformat(),
    }
    return (await post(api, dev, "/loans", body, 201)).json()["id"]


async def test_an_emi_matching_one_schedule_is_split_and_only_interest_is_expense(
    api: Api, device: Device
) -> None:
    a = await bank(api, device)
    lid = await loan(api, device)
    tid, _ = await add(
        api, device, a, 7, "8884.88", "debit", "HDFC LOAN EMI"
    )  # 2 days after the due date: inside 5 days
    r = await row(api, device, tid)
    assert (r["status"], r["loan_id"], D(r["emi_principal"]), D(r["emi_interest"])) == (
        "confirmed",
        lid,
        D("7884.88"),
        D("1000.00"),
    )
    s = await summary(api, device)
    assert D(s["expense"]) == D("1000.00") and set(s) == {
        "start",
        "end",
        "income",
        "expense",
        "net",
        "savings_rate_pct",
        "expense_by_category",
        "income_by_category",
        "pending_review_count",
    }
    cf = await get(api, device, "/cashflow", year=Y, month=M)
    assert D(cf["closing"]) == -EMI  # the cash outflow is the FULL amount


@pytest.mark.parametrize(
    "case", ["no_loan", "outside_window", "amount_differs", "two_matching_loans"]
)
async def test_an_emi_without_reliable_data_waits_for_review(
    api: Api, device: Device, case: str
) -> None:
    a = await bank(api, device)
    if case != "no_loan":
        await loan(api, device)
    if case == "two_matching_loans":
        await loan(api, device, "Second identical loan")
    day, amount = (
        (12, "8884.88")
        if case == "outside_window"
        else (7, "8884.89" if case == "amount_differs" else "8884.88")
    )
    tid, _ = await add(api, device, a, day, amount, "debit", "HDFC LOAN EMI")
    r = await row(api, device, tid)
    assert (r["status"], r["review_reason"], r["emi_principal"], r["loan_id"]) == (
        "pending_review",
        "emi_unverified",
        None,
        None,
    )
    s = await summary(api, device)
    assert D(s["expense"]) == 0 and s["pending_review_count"] == 1


async def test_a_confirmed_unsplit_emi_is_cash_outflow_not_recognised_expense(
    api: Api, device: Device
) -> None:
    a = await bank(api, device)
    tid, _ = await add(api, device, a, 7, "8884.88", "debit", "HDFC LOAN EMI")
    r = (await post(api, device, f"/transactions/{tid}/confirm", {})).json()
    assert (r["status"], r["emi_principal"]) == ("confirmed", None)
    assert (
        D((await summary(api, device))["expense"]) == 0
    )  # NOT automatically recognised as expense
    cf = await get(api, device, "/cashflow", year=Y, month=M)
    assert D(cf["closing"]) == -EMI  # but the confirmed cash outflow is preserved
    # the unallocated repayment stays visible in the transaction view: a confirmed EMI with no split
    assert [
        (t["kind"], t["status"], t["emi_principal"])
        for t in await get(api, device, "/transactions")
    ] == [("emi", "confirmed", None)]


async def test_a_manual_emi_split_is_validated_then_recognised(api: Api, device: Device) -> None:
    a = await bank(api, device)
    lid = await loan(api, device)
    tid, _ = await add(
        api, device, a, 12, "8884.88", "debit", "HDFC LOAN EMI"
    )  # outside the window: unverified
    await post(api, device, f"/transactions/{tid}/confirm", {})
    url = f"/transactions/{tid}/emi-split"
    wrong_sum = await post(api, device, url, {"principal": "7000.00", "interest": "1000.00"}, None)
    assert wrong_sum.status_code == 422 and wrong_sum.json()["error"]["code"] == "invalid_emi_split"
    assert (
        await post(api, device, url, {"principal": "-1.00", "interest": "8885.88"}, None)
    ).status_code == 422
    foreign = await post(
        api,
        device,
        url,
        {"principal": "7884.88", "interest": "1000.00", "loan_id": str(uuid.uuid4())},
        None,
    )
    assert foreign.status_code == 422 and foreign.json()["error"]["code"] == "invalid_emi_split"
    other_dev = await dev2(api)
    other_loan = await loan(api, other_dev)
    cross = await post(
        api,
        device,
        url,
        {"principal": "7884.88", "interest": "1000.00", "loan_id": other_loan},
        None,
    )
    assert cross.status_code == 422 and cross.json()["error"]["code"] == "invalid_emi_split"
    assert (
        D((await summary(api, device))["expense"]) == 0
    )  # nothing changed by the rejected attempts
    ok = (
        await post(
            api, device, url, {"principal": "7884.88", "interest": "1000.00", "loan_id": lid}
        )
    ).json()
    assert (
        D(ok["emi_principal"]),
        D(ok["emi_interest"]),
        ok["loan_id"],
        D(ok["classification_confidence"]),
    ) == (D("7884.88"), D("1000.00"), lid, D("1.00"))
    assert D((await summary(api, device))["expense"]) == D("1000.00")
    grocery, _ = await add(api, device, a, 13, "50.00", "debit", "BIGBASKET")
    notemi = await post(
        api,
        device,
        f"/transactions/{grocery}/emi-split",
        {"principal": "25.00", "interest": "25.00"},
        None,
    )
    assert notemi.status_code == 422 and notemi.json()["error"]["code"] == "invalid_emi_split"
    await links_ok(api)


async def test_recategorising_an_emi_clears_its_split(api: Api, device: Device) -> None:
    a = await bank(api, device)
    await loan(api, device)
    tid, _ = await add(api, device, a, 7, "8884.88", "debit", "HDFC LOAN EMI")
    assert (await row(api, device, tid))["emi_principal"] is not None
    r = (
        await post(api, device, f"/transactions/{tid}/category", {"category": "investments"})
    ).json()
    assert (r["kind"], r["emi_principal"], r["emi_interest"], r["loan_id"]) == (
        "expense",
        None,
        None,
        None,
    )
    assert D((await summary(api, device))["expense"]) == EMI
    await links_ok(api)
