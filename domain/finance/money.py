"""Exact money and date parsing. Everything is Decimal; floats are never used for amounts."""

import re
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
MAX_AMOUNT = Decimal("1000000000")  # 100 crore: anything larger is treated as a parsing error
_CCY = re.compile(r"(?i)(₹|\binr\b|\brs\.?|\brupees?\b)")  # the ONLY currency V1 supports: INR
# Any other currency marker (symbol, ISO code or name) is REJECTED, never stripped: 'USD 100' must not become INR 100.
_CODES = (
    "usd|eur|gbp|aed|sgd|cad|aud|jpy|cny|chf|sar|qar|kwd|bhd|omr|npr|lkr|bdt|pkr|myr|thb|hkd|nzd"
)
_NAMES = "dollars?|euros?|pounds?|dirhams?|yen|bucks"
# A symbol, or a code/name directly beside a number ('USD 5', '5 EUR', '5 dollars'); merchant words are not matched.
_OTHER_CCY = re.compile(rf"(?i)([$€£¥]|\b(?:{_CODES})\b\s*[0-9]|[0-9]\s*(?:{_CODES}|{_NAMES})\b)")


def has_other_currency(text: str) -> bool:
    return bool(_OTHER_CCY.search(text))


class ParseError(ValueError):
    pass


def q(value: Decimal) -> Decimal:
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


def parse_amount(raw: str | int | float | Decimal) -> Decimal:
    """'₹1,23,456.789' -> Decimal('123456.79'). Rejects NaN/inf, empty and absurd values.
    Sign is preserved ('(500)' and '-500' are negative); callers decide what that means."""
    if isinstance(raw, Decimal):
        val = raw
    else:
        s = str(raw).strip()
        if _OTHER_CCY.search(s):
            raise ParseError("unsupported_currency")
        neg = s.startswith("(") and s.endswith(")") or s.startswith("-")
        s = (
            _CCY.sub("", s)
            .replace(",", "")
            .replace("(", "")
            .replace(")", "")
            .replace("-", "")
            .strip()
        )
        s = re.sub(r"(?i)\s*(cr|dr)\.?$", "", s).strip()
        if not re.fullmatch(r"\d+(\.\d+)?|\.\d+", s):
            raise ParseError("not an amount")
        try:
            val = Decimal(s)
        except InvalidOperation as exc:
            raise ParseError("not an amount") from exc
        val = -val if neg else val
    if not val.is_finite() or abs(val) >= MAX_AMOUNT:
        raise ParseError("amount out of range")
    return q(val)


_MONTHS = {
    m: i
    for i, m in enumerate(
        ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1
    )
}
_FORMATS = ("%d-%m-%Y", "%d/%m/%Y", "%d.%m.%Y", "%Y-%m-%d", "%d-%m-%y", "%d/%m/%y", "%d.%m.%y")


def parse_date(raw: str, *, today: date | None = None) -> date:
    """Day-first (Indian) by default. Accepts 12-03-2024, 12/03/24, 2024-03-12, 12 Mar 2024, 12-Mar-24, 12Mar24.
    A date in the future (more than 1 day ahead) is rejected: statements and alerts are never forward-dated."""
    s = raw.strip()
    m = re.fullmatch(r"(\d{1,2})[\s\-/.]?([A-Za-z]{3})[A-Za-z]*[\s\-/.,]*(\d{2}|\d{4})", s)
    d: date | None = None
    if m and m.group(2).lower() in _MONTHS:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            d = date(y, _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            d = None
    if d is None:
        for fmt in _FORMATS:
            try:
                d = datetime.strptime(s, fmt).date()
                break
            except ValueError:
                continue
    if d is None:
        raise ParseError("not a date")
    today = today or date.today()
    if (d - today).days > 1 or d.year < 1990:
        raise ParseError("date out of range")
    return d
