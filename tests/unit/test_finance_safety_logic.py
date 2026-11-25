"""S2.1A finance classification safety: pure logic (no database). Synthetic data only.

Invariants under test: a central low-confidence threshold with an exact boundary; deterministic card-payment and
own-account transfer evidence that never matches ordinary merchants (RENT TRANSFER stays rent); INR-only parsing;
account-aware statement direction; and EMI reporting (interest is expense, principal is not, an unsplit confirmed EMI is
cash outflow but not expense, a legacy EMI is unchanged).
"""

from datetime import date
from decimal import Decimal as D

import pytest

from domain.finance import analytics as an
from domain.finance import importers
from domain.finance.importers import AmbiguousDirection
from domain.finance.money import ParseError, parse_amount
from domain.finance.normalize import (
    CLASSIFICATION_REVIEW_THRESHOLD,
    categorise,
    classify,
    is_low_confidence,
)
from domain.finance.notifications import parse_notification
from domain.finance.voice import parse_voice

TODAY = date(2024, 3, 20)


# ------------------------------------------------------------------------------------------ confidence threshold
def test_threshold_is_central_and_the_boundary_is_exact() -> None:
    assert D("0.50") == CLASSIFICATION_REVIEW_THRESHOLD
    assert is_low_confidence(D("0.00")) and is_low_confidence(D("0.49"))
    assert not is_low_confidence(D("0.50"))  # exactly the threshold is NOT low
    assert not is_low_confidence(D("0.80")) and not is_low_confidence(D("1.00"))


def test_classification_confidence_is_decimal_and_separate_from_the_old_scale() -> None:
    c = classify("SWIGGY", "debit", "bank")
    assert isinstance(c.confidence, D) and c.confidence == D("0.80")
    assert classify("SWIGGY", "debit", "bank", "groceries").confidence == D(
        "1.00"
    )  # the user's choice
    assert classify("mystery", "debit", "bank").confidence == D("0.00")
    assert categorise("mystery", "debit").confidence == 0.0  # the existing function is unchanged


# ------------------------------------------------------------------------------------------ decision table
@pytest.mark.parametrize(
    ("desc", "direction", "kind", "category", "ckind", "conf", "evidence"),
    [
        (
            "RENT TRANSFER",
            "debit",
            "bank",
            "rent",
            "expense",
            "0.80",
            False,
        ),  # the golden fixture stays rent
        ("NEFT TO SELF HDFC SAVINGS", "debit", "bank", "transfers", "transfer", "0.80", True),
        ("CREDIT CARD BILL PAYMENT HDFC", "debit", "bank", "transfers", "transfer", "0.80", True),
        ("CC PAYMENT HDFC CARD XX1234", "debit", "bank", "transfers", "transfer", "0.80", True),
        ("HDFC CARD PAYMENT", "debit", "bank", "transfers", "transfer", "0.80", True),
        (
            "AMAZON PAY ICICI CREDIT CARD BILL",
            "debit",
            "bank",
            "transfers",
            "transfer",
            "0.80",
            True,
        ),
        (
            "SELF TRANSFER TO SAVINGS - STORE CREDIT",
            "debit",
            "bank",
            "transfers",
            "transfer",
            "0.80",
            True,
        ),
        ("ELECTRICITY BILL PAYMENT BESCOM", "debit", "bank", "utilities", "expense", "0.80", False),
        ("PAYMENT TO CARD SHOP", "debit", "bank", "shopping", "expense", "0.80", False),
        ("UPI/222222/RAJESH K", "debit", "bank", "uncategorized", "expense", "0.00", False),
        ("AMAZON PURCHASE", "debit", "credit_card", "shopping", "expense", "0.80", False),
        (
            "PAYMENT RECEIVED THANK YOU",
            "credit",
            "credit_card",
            "transfers",
            "transfer",
            "0.80",
            True,
        ),
        ("PAYMENT RECD BBPS", "credit", "credit_card", "transfers", "transfer", "0.80", True),
        ("REFUND AMAZON", "credit", "credit_card", "refunds", "refund", "0.80", False),
        ("ANNUAL FEE REVERSAL", "credit", "credit_card", "refunds", "refund", "0.80", False),
        (
            "REFUND PAYMENT RECEIVED",
            "credit",
            "credit_card",
            "other_income",
            "income",
            "0.00",
            False,
        ),
        ("CREDIT VOUCHER", "credit", "credit_card", "other_income", "income", "0.00", False),
        ("ACME SALARY", "credit", "bank", "salary", "income", "0.80", False),
        ("INTEREST CREDIT", "credit", "bank", "interest", "income", "0.80", False),
        ("REFUND SWIGGY", "credit", "bank", "refunds", "refund", "0.80", False),
        ("SELF TRANSFER FROM SAVINGS", "credit", "bank", "transfers", "transfer", "0.80", True),
        # ordinary incoming payments are NEVER transfers or income: they are unknown and go to review
        ("PAYMENT RECEIVED FROM CLIENT", "credit", "bank", "other_income", "income", "0.00", False),
        ("UPI/222222/RAJESH K", "credit", "bank", "other_income", "income", "0.00", False),
        ("LOAN DISBURSAL", "credit", "bank", "other_income", "income", "0.00", False),
    ],
)
def test_classification_decision_table(
    desc: str, direction: str, kind: str, category: str, ckind: str, conf: str, evidence: bool
) -> None:
    c = classify(desc, direction, kind)
    assert (c.category, c.kind, c.confidence) == (category, ckind, D(conf))
    assert c.transfer_evidence is evidence


def test_a_user_choice_of_transfers_is_an_assertion_not_pattern_evidence() -> None:
    c = classify("UPI/222222/RAJESH K", "debit", "bank", "transfers")
    assert (c.kind, c.confidence, c.transfer_evidence) == ("transfer", D("1.00"), False)


# ------------------------------------------------------------------------------------------ INR only
@pytest.mark.parametrize(
    "raw", ["USD 100.00", "$50", "€5", "£5", "100 EUR", "INR $5", "Rs 5 USD", "5 dollars"]
)
def test_other_currencies_are_rejected_not_stripped(raw: str) -> None:
    with pytest.raises(ParseError, match="unsupported_currency"):
        parse_amount(raw)


@pytest.mark.parametrize(
    ("raw", "want"),
    [
        ("₹1,200.50", D("1200.50")),
        ("Rs. 500", D("500.00")),
        ("INR 75", D("75.00")),
        ("1,000.00 Cr", D("1000.00")),
    ],
)
def test_inr_forms_still_parse(raw: str, want: D) -> None:
    assert parse_amount(raw) == want


def test_voice_and_notifications_reject_other_currencies() -> None:
    with pytest.raises(ParseError, match="unsupported_currency"):
        parse_voice("spent 50 dollars on coffee", TODAY)
    assert parse_voice("spent 250 rupees on coffee", TODAY).amount == D("250.00")
    assert parse_notification("USD 5.00 debited from A/c XX1234 on 12-03-24 at SHOP", TODAY) is None
    assert (
        parse_notification("Rs.500.00 debited from A/c XX1234 on 12-03-24 at SHOP ($5 fee)", TODAY)
        is None
    )
    assert (
        parse_notification("Rs.500.00 debited from A/c XX1234 on 12-03-24 at SHOP", TODAY)
        is not None
    )


def test_a_currency_column_with_another_currency_rejects_that_row_only() -> None:
    csv = (
        b"Date,Description,Debit,Credit,Currency\n"
        b"01-03-2024,COFFEE,10.00,,INR\n"
        b"02-03-2024,HOTEL,50.00,,USD\n"
        b"03-03-2024,TEA,5.00,,\n"
    )
    r = importers.import_csv(csv, TODAY)
    assert [x.description for x in r.rows] == ["COFFEE", "TEA"]
    assert r.rejected == 1 and r.reasons == {"unsupported_currency": 1}


# ------------------------------------------------------------------------------------------ statement direction
def test_dr_cr_suffix_in_an_amount_cell_is_honoured_for_every_account_kind() -> None:
    csv = b"Date,Description,Amount\n01-03-2024,COFFEE,120.00 Dr\n02-03-2024,REFUND,120.00 Cr\n"
    for kind in ("bank", "credit_card", "cash"):
        r = importers.import_csv(csv, TODAY, kind)
        assert [x.direction for x in r.rows] == ["debit", "credit"], kind


def test_bank_signed_amounts_keep_the_existing_convention() -> None:
    csv = b"Date,Description,Amount\n01-03-2024,COFFEE,-120.00\n02-03-2024,SALARY,5000.00\n"
    assert [x.direction for x in importers.import_csv(csv, TODAY, "bank").rows] == [
        "debit",
        "credit",
    ]
    assert [x.direction for x in importers.import_csv(csv, TODAY).rows] == ["debit", "credit"]


def test_a_card_statement_with_unmarked_signed_amounts_is_rejected_as_ambiguous() -> None:
    csv = b"Date,Description,Amount\n05-03-2024,AMAZON,1000.00\n20-03-2024,PAYMENT RECEIVED,-1000.00\n"
    with pytest.raises(AmbiguousDirection, match="ambiguous_card_amount_sign"):
        importers.import_csv(csv, TODAY, "credit_card")


def test_a_card_statement_with_explicit_debit_credit_columns_is_supported() -> None:
    csv = b"Date,Description,Debit,Credit\n05-03-2024,AMAZON,1000.00,\n20-03-2024,PAYMENT RECEIVED,,1000.00\n"
    r = importers.import_csv(csv, TODAY, "credit_card")
    assert [(x.direction, x.amount) for x in r.rows] == [
        ("debit", D("1000.00")),
        ("credit", D("1000.00")),
    ]


def test_a_card_statement_with_a_type_column_is_supported() -> None:
    csv = b"Date,Description,Amount,Type\n05-03-2024,AMAZON,1000.00,Dr\n20-03-2024,PAYMENT,1000.00,Cr\n"
    r = importers.import_csv(csv, TODAY, "credit_card")
    assert [x.direction for x in r.rows] == ["debit", "credit"]


def test_pdf_direction_inferred_from_a_balance_change_is_refused_for_cards() -> None:
    from tests.unit.test_finance_logic import make_pdf

    pdf = make_pdf(
        [
            "01-03-2024 OPENING 100.00 1,000.00",
            "02-03-2024 AMAZON 250.00 1,250.00",
            "03-03-2024 PAYMENT 500.00 Cr 750.00",
        ]
    )
    bank = importers.import_pdf(pdf, TODAY, "bank")
    assert [x.direction for x in bank.rows][-2:] == ["credit", "credit"]  # unchanged bank behaviour
    card = importers.import_pdf(pdf, TODAY, "credit_card")
    assert [(x.description, x.direction) for x in card.rows] == [
        ("PAYMENT", "credit")
    ]  # explicit Cr only
    assert card.rejected >= 2 and "ambiguous_direction" in card.reasons


# ------------------------------------------------------------------------------------------ EMI reporting
def tv(
    d: int, amt: str, direction: str, cat: str, kind: str = "expense", **kw: object
) -> an.TxnView:
    return an.TxnView(date(2024, 3, d), D(amt), direction, cat, kind, None, None, **kw)  # type: ignore[arg-type]


def test_split_emi_counts_interest_only_as_expense_and_the_full_amount_as_cash() -> None:
    rows = [
        tv(1, "50000", "credit", "salary", "income"),
        tv(5, "8884.88", "debit", "emi_loans", "emi", emi_interest=D("1000.00")),
    ]
    s = an.summarise(rows, date(2024, 3, 1), date(2024, 3, 31))
    assert (s["income"], s["expense"], s["net"]) == (D("50000.00"), D("1000.00"), D("49000.00"))
    cf = an.cashflow(rows, date(2024, 3, 1), date(2024, 3, 10))
    assert cf["closing"] == D("50000.00") - D("8884.88")  # cash outflow is the FULL amount


def test_confirmed_unsplit_emi_is_cash_outflow_but_not_recognised_expense() -> None:
    rows = [tv(5, "8884.88", "debit", "emi_loans", "emi", unsplit_emi=True)]
    s = an.summarise(rows, date(2024, 3, 1), date(2024, 3, 31))
    assert s["expense"] == D("0.00") and s["expense_by_category"] == []
    cf = an.cashflow(rows, date(2024, 3, 1), date(2024, 3, 10))
    assert cf["closing"] == D("-8884.88")


def test_legacy_emi_views_keep_counting_in_full() -> None:
    rows = [tv(5, "8884.88", "debit", "emi_loans", "emi")]  # no split fields: today's behaviour
    s = an.summarise(rows, date(2024, 3, 1), date(2024, 3, 31))
    assert s["expense"] == D("8884.88")


def test_summary_payload_keys_are_unchanged() -> None:
    s = an.summarise([], date(2024, 3, 1), date(2024, 3, 31))
    assert set(s) == {
        "start",
        "end",
        "income",
        "expense",
        "net",
        "savings_rate_pct",
        "expense_by_category",
        "income_by_category",
    }
