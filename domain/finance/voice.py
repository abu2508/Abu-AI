"""Backend voice-entry interface: turns a TRANSCRIPT string (speech-to-text happens in the client) into a
draft transaction. Deterministic. Always reviewed by the user before it counts (speech errors are common)."""

import re
from datetime import date, timedelta

from domain.finance.money import ParseError, has_other_currency, parse_amount, parse_date
from domain.finance.types import RawTxn

_AMT = re.compile(
    r"(?:₹|rs\.?|inr|rupees?)?\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:rupees?|rs\.?|inr)?", re.I
)
_DEBIT = re.compile(r"\b(spent|paid|pay|bought|buy|gave|give|sent|purchased|charged)\b", re.I)
_CREDIT = re.compile(r"\b(received|got|earned|credited|salary|refund(?:ed)?|deposited)\b", re.I)
_DESC = re.compile(
    r"\b(?:on|for|at|to|from)\s+(.+?)(?=\s+(?:today|yesterday|on\s+\d|last)\b|$)", re.I
)


def parse_voice(transcript: str, today: date) -> RawTxn:
    t = re.sub(r"\s+", " ", transcript).strip()
    if not t or len(t) > 300:
        raise ParseError("transcript_invalid")
    if has_other_currency(t):
        raise ParseError("unsupported_currency")  # V1 is INR only: never guess a conversion
    m = _AMT.search(t)
    if not m:
        raise ParseError("no_amount")
    amount = parse_amount(m.group(1))
    if amount <= 0:
        raise ParseError("no_amount")
    d, c = bool(_DEBIT.search(t)), bool(_CREDIT.search(t))
    if d == c:
        raise ParseError("ambiguous_direction")
    when = today
    low = t.lower()
    if "day before yesterday" in low:
        when = today - timedelta(days=2)
    elif "yesterday" in low:
        when = today - timedelta(days=1)
    else:
        dm = re.search(
            r"\bon\s+(\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}|\d{1,2}\s?[A-Za-z]{3}[A-Za-z]*\s?\d{0,4})",
            t,
        )
        if dm:
            try:
                when = parse_date(
                    dm.group(1)
                    if re.search(r"\d{2,4}$", dm.group(1))
                    else f"{dm.group(1)} {today.year}",
                    today=today,
                )
            except ParseError:
                when = today
    dsc = _DESC.search(t)
    desc = (dsc.group(1) if dsc else t)[:120]
    return RawTxn(when, amount, "debit" if d else "credit", desc)
