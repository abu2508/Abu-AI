"""Deterministic finance logic. Synthetic data only. Expected numbers are worked out by hand (see comments)."""

import io
from datetime import date
from decimal import Decimal as D

import pytest

from domain.finance import analytics as an
from domain.finance import importers
from domain.finance.money import ParseError, parse_amount, parse_date
from domain.finance.normalize import categorise, normalise_merchant
from domain.finance.notifications import parse_notification
from domain.finance.types import ImportFailed
from domain.finance.voice import parse_voice

TODAY = date(2024, 3, 20)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("₹1,23,456.789", D("123456.79")),
        ("Rs. 500", D("500.00")),
        ("INR 1,200.5", D("1200.50")),
        ("(250.00)", D("-250.00")),
        ("-75", D("-75.00")),
        ("1,000.00 Cr", D("1000.00")),
        (12, D("12.00")),
    ],
)
def test_parse_amount(raw: object, want: D) -> None:
    assert parse_amount(raw) == want  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "bad", ["", "abc", "1e9", "NaN", "inf", "1.2.3", "99999999999999", "12 34"]
)
def test_parse_amount_rejects(bad: str) -> None:
    with pytest.raises(ParseError):
        parse_amount(bad)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("12-03-2024", date(2024, 3, 12)),
        ("12/03/24", date(2024, 3, 12)),
        ("2024-03-12", date(2024, 3, 12)),
        ("12 Mar 2024", date(2024, 3, 12)),
        ("12-Mar-24", date(2024, 3, 12)),
        ("12Mar24", date(2024, 3, 12)),
    ],
)
def test_parse_date_is_day_first(raw: str, want: date) -> None:
    assert parse_date(raw, today=TODAY) == want


@pytest.mark.parametrize("bad", ["31-02-2024", "nonsense", "25-12-2024", "01-01-1980"])
def test_parse_date_rejects_impossible_or_future(bad: str) -> None:
    with pytest.raises(ParseError):
        parse_date(bad, today=TODAY)


def test_normalise_merchant() -> None:
    assert normalise_merchant("UPI/402112345678/SWIGGY@icici/Food") == "SWIGGY FOOD"
    assert normalise_merchant("POS 4111XXXXXX1234 BIGBASKET  BANGALORE") == "BIGBASKET BANGALORE"
    assert normalise_merchant("  imps-Rahul   Kumar  ") == "RAHUL KUMAR"


def test_categorisation_order_and_user_rule() -> None:
    assert categorise("SWIGGY FOOD", "debit").category == "dining"
    assert categorise("HDFC LOAN EMI", "debit").kind == "emi"
    assert categorise("ACME SALARY", "credit").category == "salary"
    assert categorise("AMAZON REFUND", "credit").kind == "refund"
    assert categorise("random shop", "credit").category == "other_income"
    assert categorise("mystery", "debit").category == "uncategorized"
    assert (
        categorise("SWIGGY", "debit", "groceries").category == "groceries"
    )  # the user's choice wins


@pytest.mark.parametrize(
    ("text", "amount", "direction", "last4", "ref"),
    [
        (
            "Rs.1,234.50 debited from A/c XX1234 on 12-03-24 to VPA swiggy@icici (UPI Ref No 402112345678).",
            D("1234.50"),
            "debit",
            "1234",
            "402112345678",
        ),
        (
            "INR 500.00 debited from a/c ending 1234 on 12-Mar-24. Info: UPI/402112345678/ZOMATO",
            D("500.00"),
            "debit",
            "1234",
            None,
        ),
        (
            "Rs 50,000.00 credited to A/c XX1234 on 01-03-24 by SALARY ACME CORP. Ref 123456789",
            D("50000.00"),
            "credit",
            "1234",
            "123456789",
        ),
        (
            "Rs.2,499.00 spent on Credit Card XX5678 at AMAZON on 10-Mar-24. Avl limit Rs.50,000",
            D("2499.00"),
            "debit",
            "5678",
            None,
        ),
    ],
)
def test_notification_parsing(
    text: str, amount: D, direction: str, last4: str, ref: str | None
) -> None:
    p = parse_notification(text, TODAY)
    assert p is not None
    assert (p.txn.amount, p.txn.direction, p.txn.account_last4) == (amount, direction, last4)
    assert (p.txn.reference or None) == ref or ref is None
    assert p.confidence >= D("0.60")


@pytest.mark.parametrize(
    "text",
    [
        "Your OTP is 123456. Do not share it.",
        "Get 50% off today!",
        "Rs 500 credited and debited",
        "",
        "x" * 3000,
        "Available balance is Rs 10,000.00",
    ],
)
def test_non_transactions_are_not_parsed(text: str) -> None:
    assert parse_notification(text, TODAY) is None


def test_voice_entry() -> None:
    t = parse_voice("spent 250 rupees on coffee at starbucks yesterday", TODAY)
    assert (t.amount, t.direction, t.txn_date) == (
        D("250.00"),
        "debit",
        date(2024, 3, 19),
    ) and "coffee" in t.description
    assert parse_voice("received 5000 salary today", TODAY).direction == "credit"
    for bad in ("hello there", "spent money on coffee", "spent 250 and received 100"):
        with pytest.raises(ParseError):
            parse_voice(bad, TODAY)


# ------------------------------------------------------------------ importers (synthetic statements)
CSV = (
    b"Date,Narration,Ref No,Withdrawal Amt,Deposit Amt,Closing Balance\n"
    b"01-03-2024,UPI/SWIGGY@icici/Food,402100000001,250.00,,9750.00\n"
    b"02-03-2024,ACME SALARY,SAL001,,50000.00,59750.00\n"
    b"02-03-2024,UPI/SWIGGY@icici/Food,402100000001,250.00,,59500.00\n"
    b"not a date,broken,,1.00,,\n"
    b",,,,,\n"
)


def test_csv_import() -> None:
    r = importers.import_csv(CSV, TODAY)
    assert [(x.txn_date.day, x.amount, x.direction) for x in r.rows] == [
        (1, D("250.00"), "debit"),
        (2, D("50000.00"), "credit"),
        (2, D("250.00"), "debit"),
    ]
    assert r.rejected == 1 and r.rows[0].balance == D("9750.00")


def test_csv_signed_amount_and_type_columns() -> None:
    r = importers.import_csv(
        b"Transaction Date;Description;Amount;Type\n05/03/2024;Coffee;120.00;Dr\n06/03/2024;Refund;120.00;Cr\n07/03/2024;Misc;-80.00;\n",
        TODAY,
    )
    assert [x.direction for x in r.rows] == ["debit", "credit", "debit"]


def test_csv_without_recognisable_columns_fails_safely() -> None:
    with pytest.raises(ImportFailed):
        importers.import_csv(b"a,b,c\n1,2,3\n", TODAY)
    with pytest.raises(ImportFailed):
        importers.import_csv(b"x" * (importers.MAX_BYTES + 1), TODAY)


def test_xlsx_import_ignores_formulas_and_reads_dates() -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.append(["Date", "Particulars", "Debit", "Credit"])
    ws.append([date(2024, 3, 1), "BIGBASKET", 1500.5, None])
    ws.append([date(2024, 3, 2), "ACME SALARY", None, 50000])
    ws.append([date(2024, 3, 3), '=HYPERLINK("http://evil")', 10, None])
    buf = io.BytesIO()
    wb.save(buf)
    r = importers.import_xlsx(buf.getvalue(), TODAY)
    assert [(x.amount, x.direction) for x in r.rows] == [
        (D("1500.50"), "debit"),
        (D("50000.00"), "credit"),
        (D("10.00"), "debit"),
    ]
    with pytest.raises(ImportFailed):
        importers.import_xlsx(b"not a workbook", TODAY)


def make_pdf(lines: list[str]) -> bytes:
    """A minimal single-page PDF with one text line per row (no external libraries)."""
    stream = (
        "BT /F1 10 Tf 40 780 Td 14 TL "
        + " ".join(f"({ln.replace('(', '[').replace(')', ']')}) Tj T*" for ln in lines)
        + " ET"
    )
    objs = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        f"<< /Length {len(stream)} >>\nstream\n{stream}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out, offs = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offs.append(len(out))
        out += f"{i} 0 obj\n{o}\nendobj\n".encode()
    xref = len(out)
    out += f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode() + b"".join(
        f"{o:010d} 00000 n \n".encode() for o in offs
    )
    return (
        out + f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    )


def test_pdf_import_uses_balance_to_infer_direction() -> None:
    pdf = make_pdf(
        [
            "Statement of Account",
            "01-03-2024 UPI SWIGGY 250.00 9,750.00",
            "02-03-2024 ACME SALARY 50,000.00 59,750.00",
            "03-03-2024 ATM WDL 2,000.00 Dr 57,750.00",
            "random footer text",
        ]
    )
    r = importers.import_pdf(pdf, TODAY)
    # row 1 has no earlier balance to compare with: its direction is ambiguous so it is REJECTED, not guessed
    assert [(x.amount, x.direction) for x in r.rows] == [
        (D("50000.00"), "credit"),
        (D("2000.00"), "debit"),
    ]
    assert r.rejected == 1 and r.reasons == {"ambiguous_direction": 1}
    with pytest.raises(ImportFailed):
        importers.import_pdf(b"%PDF-1.4 garbage", TODAY)


# ------------------------------------------------------------------ exact arithmetic
def test_emi_and_amortisation_are_exact() -> None:
    # 100000 @ 12% for 12 months: r = 0.01, EMI = 100000*0.01*1.01^12/(1.01^12-1) = 8884.878867... -> 8884.88
    assert an.emi_amount(D(100000), D(12), 12) == D("8884.88")
    assert an.emi_amount(D(120000), D(0), 12) == D("10000.00")
    sched = an.amortization(D(100000), D(12), 12, date(2024, 1, 5))
    assert (
        sched[0].interest == D("1000.00")
        and sched[0].principal == D("7884.88")
        and sched[0].balance == D("92115.12")
    )
    assert sched[-1].balance == D("0.00") and len(sched) == 12
    assert sum(i.principal for i in sched) == D("100000.00")  # principal repaid exactly
    assert sched[0].due == date(2024, 2, 5) and sched[-1].due == date(2025, 1, 5)
    pos = an.loan_position(sched, date(2024, 6, 10))
    assert pos["installments_paid_per_schedule"] == 5 and pos["next_due"] == date(2024, 7, 5)
    with pytest.raises(ValueError):
        an.emi_amount(D(0), D(10), 12)


def test_add_months_clips_day() -> None:
    assert an.add_months(date(2024, 1, 31), 1) == date(2024, 2, 29) and an.add_months(
        date(2024, 11, 30), 3
    ) == date(2025, 2, 28)


def v(
    d: int,
    amt: str,
    direction: str,
    cat: str,
    kind: str = "expense",
    m: str | None = None,
    month: int = 3,
) -> an.TxnView:
    return an.TxnView(date(2024, month, d), D(amt), direction, cat, kind, m)


def test_summary_excludes_transfers_and_nets_refunds() -> None:
    txns = [
        v(1, "50000", "credit", "salary", "income"),
        v(2, "1000", "debit", "dining"),
        v(3, "300", "debit", "dining"),
        v(4, "200", "credit", "dining", "refund"),
        v(5, "9999", "debit", "transfers", "transfer"),
        v(6, "2000", "debit", "groceries"),
    ]
    s = an.summarise(txns, date(2024, 3, 1), date(2024, 3, 31))
    assert (s["income"], s["expense"], s["net"], s["savings_rate_pct"]) == (
        D("50000.00"),
        D("3100.00"),
        D("46900.00"),
        D("93.80"),
    )
    assert s["expense_by_category"] == [("groceries", D("2000.00")), ("dining", D("1100.00"))]


def test_budget_status_boundaries() -> None:
    out = an.budget_status(
        {"dining": D(800), "groceries": D(1001), "fuel": D(0)},
        {"dining": D(1000), "groceries": D(1000), "fuel": D(500), "*": D(5000)},
        D(1801),
    )
    by = {b["category"]: b for b in out}
    assert by["dining"]["status"] == "warning" and by["dining"]["pct"] == D(
        "80.00"
    )  # exactly 80% warns
    assert by["groceries"]["status"] == "over" and by["groceries"]["remaining"] == D("-1.00")
    assert by["fuel"]["status"] == "ok" and by["*"]["spent"] == D("1801.00")


def test_cashflow_running_balance_and_projection() -> None:
    txns = [
        v(1, "1000", "credit", "salary", "income"),
        v(2, "300", "debit", "dining"),
        v(2, "100", "debit", "fuel"),
    ]
    cf = an.cashflow(txns, date(2024, 3, 1), date(2024, 3, 10), D("500"))
    assert cf["closing"] == D("1100.00") and cf["series"][1]["balance"] == D("1100.00")  # type: ignore[index]
    assert cf["projected_month_expense"] == D("1240.00")  # 400 / 10 days * 31


def test_card_utilisation_bands() -> None:
    c = an.card_utilisation(
        D(1000),
        [v(1, "2000", "debit", "shopping"), v(2, "500", "credit", "transfers", "transfer")],
        D(10000),
    )
    assert (
        c["owed"] == D("2500.00") and c["utilisation_pct"] == D("25.00") and c["band"] == "healthy"
    )
    assert an.card_utilisation(D(0), [v(1, "7000", "debit", "x")], D(10000))["band"] == "high"
    assert an.card_utilisation(D(0), [], None)["utilisation_pct"] is None


def test_subscription_detection() -> None:
    txns = [v(5, "199", "debit", "subscriptions", m="NF", month=m) for m in (1, 2, 3)]
    txns += [
        v(9, "50", "debit", "dining", m="X", month=1),
        v(20, "80", "debit", "dining", m="X", month=2),
    ]  # only 2 + irregular
    txns += [
        v(1, "100", "debit", "x", m="V", month=1),
        v(2, "500", "debit", "x", m="V", month=2),
        v(3, "100", "debit", "x", m="V", month=3),
    ]  # amounts vary
    subs = an.detect_subscriptions(txns, date(2024, 3, 20))
    assert [(s.merchant_hash, s.amount, s.period, s.next_due) for s in subs] == [
        ("NF", D("199.00"), "monthly", date(2024, 4, 5))
    ]


def test_insights_are_numeric_and_templated() -> None:
    cur = an.summarise(
        [
            v(1, "20000", "credit", "salary", "income"),
            v(2, "19500", "debit", "shopping"),
            v(3, "6000", "debit", "dining"),
        ],
        date(2024, 3, 1),
        date(2024, 3, 31),
    )
    prev = an.summarise(
        [v(2, "2000", "debit", "dining", month=2)], date(2024, 2, 1), date(2024, 2, 29)
    )
    budgets = an.budget_status({"shopping": D(19500)}, {"shopping": D(15000)}, D(25500))
    ins = an.insights(
        cur,
        prev,
        budgets,
        [],
        [{"kind": "emi", "amount": D("8884.88"), "due": date(2024, 3, 25)}],
        date(2024, 3, 20),
    )
    codes = [i.code for i in ins]
    assert codes[0] in ("spending_exceeds_income", "budget_exceeded") and {
        "spending_exceeds_income",
        "budget_exceeded",
        "category_increase",
        "upcoming_due",
        "top_category",
    } <= set(codes)
    texts = {i.code: an.render_insight(i) for i in ins}
    assert (
        texts["spending_exceeds_income"] == "You spent ₹5500.00 more than you earned this period."
    )
    assert "200.00%" in texts["category_increase"]
