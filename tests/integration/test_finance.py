# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return"
"""Finance API + service against a real database. Synthetic data only."""

import base64
import io
import json
import uuid
from datetime import date, timedelta
from decimal import Decimal as D

import pytest
from sqlalchemy import func, select, text

from database.schemas import FinTransaction
from tests.integration.conftest import Api
from tests.integration.helpers import Device, enroll_device, make_user
from tests.unit.test_finance_logic import CSV, make_pdf
from tests.unit.test_http_hardening import _routes

# Last month (whole month in the past), so dates are never "future" regardless of when the test runs.
_first = date.today().replace(day=1)
_last = _first - timedelta(days=1)
Y, M = _last.year, _last.month


def d(day: int) -> str:
    return f"{Y}-{M:02d}-{day:02d}"


def dmy(day: int) -> str:
    return f"{day:02d}-{M:02d}-{Y}"


async def dev2(api: Api, name: str = "B") -> Device:
    return await enroll_device(
        api.sessions, api.settings, api.client, await make_user(api.sessions, name)
    )


async def post(api: Api, dev: Device, path: str, body: object, expect: int | None = 200):
    r = await api.client.post(f"/v1/finance{path}", json=body, headers=dev.headers)
    if expect:
        assert r.status_code == expect, r.text
    return r


async def get(api: Api, dev: Device, path: str, **params: object):
    r = await api.client.get(f"/v1/finance{path}", params=params, headers=dev.headers)
    assert r.status_code == 200, r.text
    return r.json()


async def bank(api: Api, dev: Device, **kw: object) -> str:
    body = {"kind": "bank", "label": "Salary account", "institution": "hdfc", "last4": "1234", **kw}
    return (await post(api, dev, "/accounts", body, 201)).json()["id"]


def txn(
    account: str,
    day: int,
    amount: str,
    direction: str,
    desc: str,
    ref: str | None = None,
    **kw: object,
) -> dict[str, object]:
    return {
        "account_id": account,
        "date": d(day),
        "amount": amount,
        "direction": direction,
        "description": desc,
        "reference": ref,
        **kw,
    }


# ---- authentication and isolation -----------------------------------------------------------------------------
async def test_every_finance_route_needs_authentication(api: Api) -> None:
    routes = [(m, p) for m, p in _routes(api.app) if p.startswith("/v1/finance")]
    assert len(routes) >= 25
    for method, path in routes:
        resp = await api.client.request(method, path, json={})
        assert resp.status_code == 401, (method, path)


async def test_users_cannot_see_or_touch_each_others_finance_data(api: Api, device: Device) -> None:
    b = await dev2(api)
    acct = await bank(api, device)
    t = (
        await post(
            api, device, "/transactions", txn(acct, 3, "100.00", "debit", "coffee shop"), 201
        )
    ).json()["transaction_id"]
    assert await get(api, b, "/accounts") == [] and await get(api, b, "/transactions") == []
    assert (
        await post(api, b, "/transactions", txn(acct, 4, "5.00", "debit", "x"), None)
    ).status_code == 404  # someone else's account
    for path in (f"/transactions/{t}/confirm", f"/transactions/{t}/reject"):
        assert (await post(api, b, path, {} if "confirm" in path else None, None)).status_code in (
            404,
            409,
        )
    assert (
        await api.client.delete(f"/v1/finance/transactions/{t}", headers=b.headers)
    ).status_code == 404
    assert (
        await api.client.patch(
            f"/v1/finance/accounts/{acct}", json={"archived": True}, headers=b.headers
        )
    ).status_code == 404
    assert len(await get(api, device, "/transactions")) == 1  # untouched


async def test_strict_validation(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    for bad in (
        {"amount": "-5"},
        {"amount": "0"},
        {"amount": "abc"},
        {"amount": "1.234"},
        {"direction": "sideways"},
        {"extra": 1},
        {"date": "garbage"},
    ):
        r = await post(
            api, device, "/transactions", {**txn(acct, 3, "10.00", "debit", "x"), **bad}, None
        )
        assert r.status_code == 422, bad
    assert (
        await post(api, device, "/accounts", {"kind": "credit_card", "label": "c"}, None)
    ).status_code == 422
    assert (
        await post(api, device, "/accounts", {"kind": "bank", "label": "x", "last4": "12a4"}, None)
    ).status_code == 422
    assert (
        await post(
            api,
            device,
            "/transactions",
            txn(acct, 3, "1.00", "debit", "x", category="nonsense"),
            None,
        )
    ).status_code == 422


# ---- manual entry, summary exactness ---------------------------------------------------------------------------------
async def test_summary_budget_and_insights_are_exact(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    for day, amt, direc, desc in [
        (1, "50000.00", "credit", "ACME SALARY"),
        (2, "1200.50", "debit", "BIGBASKET"),
        (3, "799.50", "debit", "SWIGGY"),
        (4, "250.00", "debit", "mystery"),
    ]:
        await post(
            api, device, "/transactions", txn(acct, day, amt, direction=direc, desc=desc), 201
        )
    s = await get(api, device, "/summary", year=Y, month=M)
    assert (D(s["income"]), D(s["expense"]), D(s["net"])) == (
        D("50000.00"),
        D("2250.00"),
        D("47750.00"),
    )
    assert D(s["savings_rate_pct"]) == D("95.50")
    cats = {c: D(v) for c, v in s["expense_by_category"]}
    assert cats == {"groceries": D("1200.50"), "dining": D("799.50"), "uncategorized": D("250.00")}
    await api.client.put(
        "/v1/finance/budgets",
        json={"category": "groceries", "monthly_limit": "1000"},
        headers=device.headers,
    )
    b = await get(api, device, "/budgets", year=Y, month=M)
    assert b[0]["status"] == "over" and D(b[0]["remaining"]) == D("-200.50")
    ins = await get(api, device, "/insights", year=Y, month=M)
    assert any(i["code"] == "budget_exceeded" for i in ins) and all(
        isinstance(i["text"], str) for i in ins
    )
    cf = await get(api, device, "/cashflow", year=Y, month=M)
    assert D(cf["closing"]) == D("47750.00")


# ---- notification ingestion --------------------------------------------------------------------------------------------
async def test_notification_flow_pending_then_confirmed(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    sms = f"Rs.1,234.50 debited from A/c XX1234 on {dmy(10)} to VPA swiggy@icici (UPI Ref No 402112345678)."
    r = (await post(api, device, "/ingest", {"channel": "sms", "text": sms})).json()
    assert (
        r["status"] == "inserted"
        and r["transaction_status"] == "pending_review"
        and D(r["confidence"]) >= D("0.9")
    )
    s = await get(api, device, "/summary", year=Y, month=M)
    assert D(s["expense"]) == 0 and s["pending_review_count"] == 1  # unreviewed alerts never count
    assert (await post(api, device, "/ingest", {"channel": "sms", "text": sms})).json()[
        "status"
    ] == "duplicate"  # same alert twice
    q = await get(api, device, "/review")
    assert len(q["pending_review"]) == 1 and q["pending_review"][0]["category"] == "dining"
    tid = q["pending_review"][0]["id"]
    await post(
        api, device, f"/transactions/{tid}/confirm", {"category": "groceries", "remember": True}
    )
    assert D((await get(api, device, "/summary", year=Y, month=M))["expense"]) == D("1234.50")
    # the remembered merchant rule now categorises the next SWIGGY alert as groceries
    sms2 = f"Rs.99.00 debited from A/c XX1234 on {dmy(11)} to VPA swiggy@icici (UPI Ref No 402112345999)."
    r2 = (await post(api, device, "/ingest", {"channel": "shortcut", "text": sms2})).json()
    t2 = [t for t in await get(api, device, "/transactions") if t["id"] == r2["transaction_id"]][0]
    assert t2["category"] == "groceries" and t2["source"] == "shortcut"
    assert acct


async def test_notification_edge_cases(api: Api, device: Device) -> None:
    await bank(api, device)
    otp = (
        await post(
            api,
            device,
            "/ingest",
            {"channel": "sms", "text": "Your OTP is 482913. Do not share it."},
        )
    ).json()
    assert otp["status"] == "unparsed"
    unknown = (
        await post(
            api,
            device,
            "/ingest",
            {"channel": "email", "text": f"Rs.10.00 debited from A/c XX9999 on {dmy(3)} at SHOP"},
        )
    ).json()
    assert unknown["status"] == "needs_account" and await get(api, device, "/transactions") == []
    assert (
        await post(api, device, "/ingest", {"channel": "carrier_pigeon", "text": "x"}, None)
    ).status_code == 422
    assert (
        await post(api, device, "/ingest", {"channel": "sms", "text": "x" * 2001}, None)
    ).status_code == 422


async def test_auto_confirm_account_and_bulk_confirm(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    await api.client.patch(
        f"/v1/finance/accounts/{acct}", json={"auto_confirm": True}, headers=device.headers
    )
    r = (
        await post(
            api,
            device,
            "/ingest",
            {
                "channel": "sms",
                "text": f"Rs.75.00 debited from A/c XX1234 on {dmy(5)} at KIOSK Ref 99887766",
            },
        )
    ).json()
    assert r["transaction_status"] == "confirmed"
    await api.client.patch(
        f"/v1/finance/accounts/{acct}", json={"auto_confirm": False}, headers=device.headers
    )
    await post(
        api,
        device,
        "/ingest",
        {
            "channel": "sms",
            "text": f"Rs.80.00 debited from A/c XX1234 on {dmy(6)} at KIOSK Ref 99887755",
        },
    )
    assert (await post(api, device, "/transactions/confirm-pending", None)).json() == {
        "confirmed": 1
    }


async def test_credit_card_matching_and_utilisation(api: Api, device: Device) -> None:
    await bank(api, device, last4="5678")
    card = (
        await post(
            api,
            device,
            "/accounts",
            {
                "kind": "credit_card",
                "label": "Card",
                "last4": "5678",
                "credit_limit": "10000",
                "institution": "icici",
            },
            201,
        )
    ).json()["id"]
    r = (
        await post(
            api,
            device,
            "/ingest",
            {
                "channel": "sms",
                "text": f"Rs.2,500.00 spent on Credit Card XX5678 at AMAZON on {dmy(7)}. Avl limit Rs.7,500",
            },
        )
    ).json()
    assert r["status"] == "inserted"
    await post(api, device, f"/transactions/{r['transaction_id']}/confirm", {})
    c = await get(api, device, "/cards")
    assert (
        c[0]["account_id"] == card
        and D(c[0]["utilisation_pct"]) == D("25.00")
        and c[0]["band"] == "healthy"
    )


# ---- duplicates and reconciliation -----------------------------------------------------------------------------------------
async def test_fuzzy_duplicate_is_flagged_not_dropped_and_resolvable(
    api: Api, device: Device
) -> None:
    acct = await bank(api, device)
    a = (
        await post(
            api, device, "/transactions", txn(acct, 8, "250.00", "debit", "STARBUCKS COFFEE"), 201
        )
    ).json()
    b = (
        await post(api, device, "/transactions", txn(acct, 8, "250.00", "debit", "Starbucks"), 201)
    ).json()
    assert a["transaction_status"] == "confirmed" and b["transaction_status"] == "duplicate_suspect"
    assert D((await get(api, device, "/summary", year=Y, month=M))["expense"]) == D(
        "250.00"
    )  # suspect not counted
    resp = await post(
        api, device, f"/transactions/{b['transaction_id']}/confirm", {}
    )  # two real coffees
    assert resp.json()["status"] == "confirmed"
    assert D((await get(api, device, "/summary", year=Y, month=M))["expense"]) == D("500.00")


async def test_same_reference_is_an_exact_duplicate(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    await post(
        api, device, "/transactions", txn(acct, 8, "75.00", "debit", "BOOKS", ref="REF123456"), 201
    )
    r = (
        await post(
            api,
            device,
            "/transactions",
            txn(acct, 8, "75.00", "debit", "BOOKS", ref="REF123456"),
            201,
        )
    ).json()
    assert r["status"] == "duplicate" and len(await get(api, device, "/transactions")) == 1


def csv_b64(body: bytes) -> str:
    return base64.b64encode(body).decode()


async def test_csv_import_reconciliation_and_reimport(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    # An alert arrived first ...
    await post(
        api,
        device,
        "/ingest",
        {
            "channel": "sms",
            "text": f"Rs.250.00 debited from A/c XX1234 on {dmy(1)}. Info: UPI/402100000001/SWIGGY",
        },
    )
    csv = CSV.replace(b"01-03-2024", dmy(1).encode()).replace(b"02-03-2024", dmy(2).encode())
    r = (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "csv", "content_base64": csv_b64(csv)},
        )
    ).json()
    assert (r["parsed"], r["imported"], r["reconciled"], r["rejected"], r["duplicates"]) == (
        4,
        2,
        1,
        1,
        0,
    )
    assert r["reconciled"] == 1  # the alert and the statement row are one transaction, not two
    txns = await get(api, device, "/transactions")
    assert (
        sum(1 for t in txns if t["direction"] == "debit" and t["amount"] == "250.00") == 2
    )  # 1st (reconciled) + a legitimately separate one on day 2
    assert any(t["reconciled"] for t in txns)
    again = await post(
        api,
        device,
        "/import",
        {"account_id": acct, "format": "csv", "content_base64": csv_b64(csv)},
        None,
    )
    assert again.status_code == 409 and again.json()["error"]["code"] == "already_imported"
    # an overlapping statement (different file, same rows) adds nothing new
    csv2 = csv + b"09-09-2020,OLD ROW,,1.00,,\n"
    overlap = (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "csv", "content_base64": csv_b64(csv2)},
        )
    ).json()
    assert overlap["duplicates"] >= 2


async def test_import_hostile_inputs(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    base = {"account_id": acct, "format": "csv"}
    assert (
        await post(api, device, "/import", {**base, "content_base64": "###"}, None)
    ).status_code == 422
    assert (
        await post(api, device, "/import", {**base, "content_base64": csv_b64(b"a,b\n1,2\n")}, None)
    ).status_code == 422
    big = csv_b64(b"x" * (3 * 1024 * 1024 + 1))
    assert (
        await post(api, device, "/import", {**base, "content_base64": big}, None)
    ).status_code in (413, 422)
    assert (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "xlsx", "content_base64": csv_b64(b"not xlsx")},
            None,
        )
    ).status_code == 422
    assert (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "exe", "content_base64": csv_b64(b"x")},
            None,
        )
    ).status_code == 422
    huge = csv_b64(
        b"x" * (4 * 1024 * 1024)
    )  # over the route limit: rejected by the size middleware
    assert (
        await post(api, device, "/import", {**base, "content_base64": huge}, None)
    ).status_code == 413
    assert (await get(api, device, "/transactions")) == []


async def test_xlsx_and_pdf_import(api: Api, device: Device) -> None:
    from openpyxl import Workbook

    acct = await bank(api, device)
    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Narration", "Debit", "Credit"])
    ws.append([date(Y, M, 3), "DMART GROCER", 800, None])
    ws.append([date(Y, M, 4), "ACME SALARY", None, 40000])
    buf = io.BytesIO()
    wb.save(buf)
    x = (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "xlsx", "content_base64": csv_b64(buf.getvalue())},
        )
    ).json()
    assert x["imported"] == 2
    pdf = make_pdf(
        [f"{dmy(6)} CAFE 100.00 Dr 39,900.00", f"{dmy(7)} REFUND AMAZON 50.00 Cr 39,950.00"]
    )
    p = (
        await post(
            api,
            device,
            "/import",
            {"account_id": acct, "format": "pdf", "content_base64": csv_b64(pdf)},
        )
    ).json()
    assert p["imported"] == 2
    s = await get(api, device, "/summary", year=Y, month=M)
    assert D(s["income"]) == D("40000.00") and D(s["expense"]) == D(
        "850.00"
    )  # 800 + 100 - 50 refund


# ---- voice, loans, subscriptions ------------------------------------------------------------------------------------------------
async def test_voice_entry_waits_for_review(api: Api, device: Device) -> None:
    acct = await bank(api, device)
    r = (
        await post(
            api,
            device,
            "/transactions/voice",
            {"account_id": acct, "transcript": "spent 250 rupees on coffee at starbucks yesterday"},
            201,
        )
    ).json()
    assert r["transaction_status"] == "pending_review" and D(r["confidence"]) == D("0.70")
    assert (
        await post(
            api,
            device,
            "/transactions/voice",
            {"account_id": acct, "transcript": "blah blah"},
            None,
        )
    ).status_code == 422


async def test_loans_and_subscriptions(api: Api, device: Device) -> None:
    loan = (
        await post(
            api,
            device,
            "/loans",
            {
                "label": "Car loan",
                "principal": "100000",
                "annual_rate_pct": "12",
                "tenor_months": 12,
                "start_date": d(5),
            },
            201,
        )
    ).json()
    assert D(loan["emi"]) == D("8884.88") and loan["installments_total"] == 12
    sched = await get(api, device, f"/loans/{loan['id']}/schedule")
    assert D(sched[-1]["balance"]) == 0 and len(sched) == 12
    assert (
        await api.client.get(f"/v1/finance/loans/{uuid.uuid4()}/schedule", headers=device.headers)
    ).status_code == 404
    soon = date.today() + timedelta(days=3)
    await post(
        api,
        device,
        "/subscriptions",
        {"label": "Streaming", "amount": "199", "period": "monthly", "next_due": soon.isoformat()},
        201,
    )
    up = await get(api, device, "/upcoming", days=7)
    assert any(i["kind"] == "subscription" and D(i["amount"]) == D("199.00") for i in up)
    subs = await get(api, device, "/subscriptions")
    sid = subs["active"][0]["id"]
    assert (await post(api, device, f"/subscriptions/{sid}/cancel", None)).json()[
        "status"
    ] == "cancelled"
    assert (await get(api, device, "/subscriptions"))["active"] == []


# ---- privacy -------------------------------------------------------------------------------------------------------------------------
async def test_nothing_sensitive_is_stored_in_plaintext(api: Api, device: Device) -> None:
    acct = await bank(api, device, label="My secret label")
    sms = f"Rs.321.00 debited from A/c XX1234 on {dmy(9)} to VPA uniquemerchantname@icici (UPI Ref No 777888999000)."
    await post(api, device, "/ingest", {"channel": "sms", "text": sms})
    await post(
        api, device, "/transactions", txn(acct, 2, "5.00", "debit", "PRIVATE DESCRIPTION XYZ"), 201
    )
    async with api.sessions() as s:
        dump = ""
        for table in (
            "fin_accounts",
            "fin_transactions",
            "fin_import_batches",
            "fin_merchant_rules",
        ):
            rows = (await s.execute(text(f"SELECT * FROM {table}"))).all()
            dump += json.dumps([[str(c) for c in row] for row in rows])
        for needle in (
            "My secret label",
            "PRIVATE DESCRIPTION",
            "uniquemerchantname",
            "UPI Ref",
            "777888999000",
            "debited from",
        ):
            assert needle not in dump, needle
        n = (
            await s.execute(
                select(func.count())
                .select_from(FinTransaction)
                .where(FinTransaction.description_enc.is_not(None))
            )
        ).scalar_one()
        assert n == 2


async def test_data_entry_survives_the_kill_switch(api: Api, device: Device) -> None:
    from core.audit.service import ADMIN
    from core.governance import killswitch

    acct = await bank(api, device)
    await killswitch.activate(api.sessions, ADMIN, "drill")
    assert (
        await post(api, device, "/transactions", txn(acct, 3, "10.00", "debit", "tea"), 201)
    ).status_code == 201
    assert (await api.client.get("/v1/finance/summary", headers=device.headers)).status_code == 200


@pytest.mark.parametrize(
    "path", ["/v1/finance/pay", "/v1/finance/transfer", "/v1/finance/send", "/v1/messaging/send"]
)
async def test_no_payment_or_send_routes_exist(api: Api, device: Device, path: str) -> None:
    for method in ("POST", "PUT", "GET"):
        assert (
            await api.client.request(method, path, json={}, headers=device.headers)
        ).status_code in (404, 405)
