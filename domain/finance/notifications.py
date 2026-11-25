"""Notification ingestion: provider-independent parsing of bank SMS / email alert TEXT.

Input is plain text handed over by the user (an iOS Shortcut, the Share Extension, a pasted alert, a
forwarded email). Nothing here assumes access to the device's notifications or to any bank API.
The raw text is parsed and discarded; only the parsed fields are kept.
Formats below are SYNTHETIC examples of common alert styles, not any bank's real template: real-world
hardening happens with real samples once the deployment gates allow real data.
"""

import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Protocol

from domain.finance.money import ParseError, has_other_currency, parse_amount, parse_date
from domain.finance.types import RawTxn

MAX_TEXT = 2000
_AMT = r"(?:₹|inr|rs\.?|rupees?)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)"
_DEBIT = re.compile(r"\b(debited|spent|paid|withdrawn|purchase|used for|sent)\b", re.I)
_CREDIT = re.compile(r"\b(credited|received|deposited|refunded)\b", re.I)
_OTP = re.compile(r"\b(otp|one[- ]time password|verification code|do not share)\b", re.I)
_LAST4 = re.compile(
    r"(?:a/?c|account|card)(?:\s+no\.?)?\s*(?:ending|ending with|no\.?)?\s*[x*]*\s*(\d{4})\b", re.I
)
_DATE = re.compile(
    r"\bon\s+(\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}|\d{1,2}[\s\-/]?[A-Za-z]{3}[A-Za-z]*[\s\-/,]*\d{2,4}|\d{4}-\d{2}-\d{2})",
    re.I,
)
_REF = re.compile(
    r"\b(?:ref(?:erence)?|rrn|txn|utr)(?:\s*(?:no|id|number))?\.?\s*[:#]?\s*([A-Za-z0-9]{6,})", re.I
)
_MERCHANT = [
    re.compile(r"\bto\s+vpa\s+([A-Za-z0-9._\-]+@[A-Za-z]+)", re.I),
    re.compile(r"\binfo[:\-]\s*([^.\n]{3,60})", re.I),
    re.compile(r"\bat\s+([A-Za-z0-9&' ._\-]{2,40}?)(?=\s+on\b|\.|,|\s*$|\s+avl|\s+ref)", re.I),
    re.compile(
        r"\b(?:to|by|from)\s+([A-Za-z0-9&' ._\-]{2,40}?)(?=\s+on\b|\.|,|\s*$|\s+ref|\s+\()", re.I
    ),
]


@dataclass(frozen=True)
class Parsed:
    txn: RawTxn
    confidence: Decimal
    parser: str


class NotificationParser(Protocol):
    name: str

    def parse(self, text: str, received_on: date) -> Parsed | None: ...


class GenericAlertParser:
    """Handles the common 'amount + debited/credited + account + date + merchant + ref' alert shape."""

    name = "generic_alert"

    def parse(self, text: str, received_on: date) -> Parsed | None:
        t = re.sub(r"\s+", " ", text).strip()
        if not t or len(t) > MAX_TEXT or _OTP.search(t):
            return None
        if has_other_currency(
            t
        ):  # INR only: an alert naming another currency is not parsed (stays unparsed, never guessed)
            return None
        am = re.search(_AMT, t, re.I)
        if not am:
            return None
        is_d, is_c = bool(_DEBIT.search(t)), bool(_CREDIT.search(t))
        if is_d == is_c:  # neither, or contradictory: refuse rather than guess a direction
            return None
        try:
            amount = parse_amount(am.group(1))
        except ParseError:
            return None
        if amount <= 0:
            return None
        score = Decimal("0.60")
        dm = _DATE.search(t)
        txn_date = received_on
        if dm:
            try:
                txn_date = parse_date(dm.group(1), today=received_on)
            except ParseError:
                score -= Decimal("0.15")
        else:
            score -= Decimal("0.15")  # fell back to the day it was received
        lm = _LAST4.search(t)
        last4 = lm.group(1) if lm else None
        score += Decimal("0.15") if last4 else Decimal(0)
        merchant = next((m.group(1).strip() for rx in _MERCHANT if (m := rx.search(t))), "")
        score += Decimal("0.15") if merchant else Decimal(0)
        rm = _REF.search(t)
        ref = rm.group(1) if rm else None
        score += Decimal("0.10") if ref else Decimal(0)
        return Parsed(
            RawTxn(
                txn_date, amount, "debit" if is_d else "credit", merchant or "unknown", ref, last4
            ),
            min(score, Decimal("1.00")).quantize(Decimal("0.01")),
            self.name,
        )


PARSERS: list[NotificationParser] = [GenericAlertParser()]


def parse_notification(text: str, received_on: date) -> Parsed | None:
    """First parser that recognises the text wins. None = not a transaction alert (OTP, promo, balance only)."""
    for p in PARSERS:
        if (res := p.parse(text, received_on)) is not None:
            return res
    return None
