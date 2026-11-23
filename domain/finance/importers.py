"""Statement importers (CSV, Excel, PDF) -> RawTxn. Untrusted files: hard size/row/page limits, no macros,
no external references, and the file itself is never stored (only a keyed hash for repeat detection)."""

import csv
import io
import re
from collections.abc import Callable
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from domain.finance.money import ParseError, parse_amount, parse_date
from domain.finance.types import ImportFailed, ImportResult, RawTxn

MAX_BYTES = 2_000_000
MAX_ROWS = 5000
MAX_PAGES = 30
MAX_SHEETS = 3
HEADER_SCAN_ROWS = 25

_SYN = {
    "date": ("date", "txn date", "transaction date", "value date", "posting date", "tran date"),
    "desc": (
        "description",
        "narration",
        "particulars",
        "details",
        "transaction remarks",
        "remarks",
    ),
    "debit": ("debit", "withdrawal", "withdrawals", "debit amount", "dr", "withdrawal amt"),
    "credit": ("credit", "deposit", "deposits", "credit amount", "cr", "deposit amt"),
    "amount": ("amount", "transaction amount", "amt"),
    "type": ("type", "dr/cr", "cr/dr", "txn type", "transaction type"),
    "ref": ("ref no", "reference", "reference no", "chq/ref no", "utr", "ref", "cheque no"),
    "balance": ("balance", "closing balance", "running balance"),
}


def _norm_header(h: Any) -> str:
    return re.sub(r"[^a-z/ ]", "", str(h or "").lower()).strip()


def _map_header(cells: list[Any]) -> dict[str, int] | None:
    found: dict[str, int] = {}
    for i, c in enumerate(cells):
        n = _norm_header(c)
        for key, names in _SYN.items():
            if n in names and key not in found:
                found[key] = i
    if (
        "date" in found
        and "desc" in found
        and (("debit" in found or "credit" in found) or "amount" in found)
    ):
        return found
    return None


def _cell_date(v: Any, today: date) -> date:
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    return parse_date(str(v), today=today)


def _getter(r: list[Any], header: dict[str, int]) -> Callable[[str], Any]:
    def get(k: str) -> Any:
        return r[header[k]] if k in header and header[k] < len(r) else None

    return get


def rows_to_txns(rows: list[list[Any]], result: ImportResult, today: date) -> None:
    header: dict[str, int] | None = None
    start = 0
    for i, r in enumerate(rows[:HEADER_SCAN_ROWS]):
        header = _map_header(list(r))
        if header:
            start = i + 1
            break
    if header is None:
        raise ImportFailed("unrecognised_columns")
    for r in rows[start : start + MAX_ROWS]:
        if not any(str(c or "").strip() for c in r):
            continue
        try:
            get = _getter(r, header)
            d = _cell_date(get("date"), today)
            desc = re.sub(r"\s+", " ", str(get("desc") or "")).strip()[:300]
            debit = credit = Decimal(0)
            if "debit" in header or "credit" in header:
                dv, cv = get("debit"), get("credit")
                debit = (
                    parse_amount(dv)
                    if str(dv or "").strip() not in ("", "0", "0.00")
                    else Decimal(0)
                )
                credit = (
                    parse_amount(cv)
                    if str(cv or "").strip() not in ("", "0", "0.00")
                    else Decimal(0)
                )
            else:
                amt = parse_amount(get("amount") or "")
                t = str(get("type") or "").strip().lower()
                if t in ("dr", "debit", "d"):
                    debit = abs(amt)
                elif t in ("cr", "credit", "c"):
                    credit = abs(amt)
                elif amt < 0:
                    debit = -amt
                else:
                    credit = amt
            if (debit > 0) == (credit > 0):  # both or neither: ambiguous, refuse
                raise ParseError("ambiguous_direction")
            ref = str(get("ref") or "").strip() or None
            bal = None
            try:
                bal = parse_amount(get("balance")) if get("balance") not in (None, "") else None
            except ParseError:
                bal = None
            result.rows.append(
                RawTxn(
                    d,
                    debit or credit,
                    "debit" if debit > 0 else "credit",
                    desc or "unknown",
                    ref and ref[:40],
                    None,
                    bal,
                )
            )
        except (ParseError, IndexError, TypeError, ValueError) as exc:
            result.reject(
                str(exc) if isinstance(exc, ParseError) and len(str(exc)) < 40 else "bad_row"
            )


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeError:
            continue
    raise ImportFailed("undecodable")


def import_csv(data: bytes, today: date | None = None) -> ImportResult:
    if len(data) > MAX_BYTES:
        raise ImportFailed("file_too_large")
    text = _decode(data)
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    result = ImportResult()
    rows = [
        list(r)
        for _, r in zip(
            range(MAX_ROWS + HEADER_SCAN_ROWS + 1),
            csv.reader(io.StringIO(text), dialect),
            strict=False,
        )
    ]
    rows_to_txns(rows, result, today or date.today())
    return result


def import_xlsx(data: bytes, today: date | None = None) -> ImportResult:
    if len(data) > MAX_BYTES:
        raise ImportFailed("file_too_large")
    from openpyxl import load_workbook

    result = ImportResult()
    try:
        wb = load_workbook(
            io.BytesIO(data), read_only=True, data_only=True
        )  # never evaluates formulas/macros
    except Exception as exc:  # noqa: BLE001 - any parser failure is just "unreadable"
        raise ImportFailed("unreadable_workbook") from exc
    try:
        found = False
        for ws in wb.worksheets[:MAX_SHEETS]:
            rows = [
                list(r)
                for _, r in zip(
                    range(MAX_ROWS + HEADER_SCAN_ROWS + 1),
                    ws.iter_rows(values_only=True),
                    strict=False,
                )
            ]
            try:
                rows_to_txns(rows, result, today or date.today())
                found = True
            except ImportFailed:
                continue
        if not found:
            raise ImportFailed("unrecognised_columns")
    finally:
        wb.close()
    return result


_PDF_ROW = re.compile(
    r"^(?P<date>\d{1,2}[\-/.]\d{1,2}[\-/.]\d{2,4}|\d{1,2}[\s\-]?[A-Za-z]{3}[\s\-]?\d{2,4})\s+(?P<rest>.+)$"
)
_MONEY = re.compile(r"(?<![\w.])\(?-?[0-9][0-9,]*\.[0-9]{2}\)?(?:\s?(?:Cr|Dr))?(?![\w.])", re.I)


def import_pdf(data: bytes, today: date | None = None) -> ImportResult:
    if len(data) > MAX_BYTES:
        raise ImportFailed("file_too_large")
    from pypdf import PdfReader

    today = today or date.today()
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ImportFailed("encrypted_pdf")
        if len(reader.pages) > MAX_PAGES:
            raise ImportFailed("too_many_pages")
        lines = [ln for p in reader.pages for ln in (p.extract_text() or "").splitlines()]
    except ImportFailed:
        raise
    except Exception as exc:  # noqa: BLE001
        raise ImportFailed("unreadable_pdf") from exc
    result = ImportResult()
    prev_balance: Decimal | None = None
    for line in lines[: MAX_ROWS * 3]:
        m = _PDF_ROW.match(re.sub(r"\s+", " ", line).strip())
        if not m:
            continue
        rest = m.group("rest")
        amounts = list(_MONEY.finditer(rest))
        if not amounts:
            continue
        try:
            d = parse_date(m.group("date"), today=today)
            desc = re.sub(r"\s+", " ", rest[: amounts[0].start()]).strip()[:300] or "unknown"
            first = amounts[0].group(0)
            amt = abs(parse_amount(first))
            bal = parse_amount(amounts[1].group(0)) if len(amounts) > 1 else None
            explicit = re.search(r"(?i)(cr|dr)$", first.strip())
            before, prev_balance = prev_balance, (bal if bal is not None else prev_balance)
            if explicit:
                direction = "credit" if explicit.group(1).lower() == "cr" else "debit"
            elif bal is not None and before is not None and bal != before:
                direction = "credit" if bal > before else "debit"
            else:
                raise ParseError("ambiguous_direction")
            result.rows.append(RawTxn(d, amt, direction, desc, None, None, bal))
        except ParseError as exc:
            result.reject(str(exc) if len(str(exc)) < 40 else "bad_row")
    return result
