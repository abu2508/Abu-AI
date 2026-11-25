"""Deterministic financial calculations. Pure functions over plain data: no database, no model, no float.
Rounding is half-up to paise at defined points only (documented per function)."""

import calendar
import statistics
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import Decimal, getcontext
from typing import cast

from domain.finance.money import q

getcontext().prec = 40
ZERO = Decimal(0)
WARN_PCT = Decimal(80)


@dataclass(frozen=True)
class TxnView:
    """Confirmed transactions only; the caller filters status. `kind == 'transfer'` is excluded from totals."""

    txn_date: date
    amount: Decimal
    direction: str
    category: str
    kind: str
    merchant_hash: str | None = None
    account_id: str | None = None
    # S2.1A. A confirmed EMI with a RELIABLE split: only the interest is recognised expense (the principal repays a
    # liability). A confirmed NEW EMI without a split is cash outflow but NOT recognised expense. Both default to the
    # legacy behaviour (the whole debit is expense), which historical rows keep.
    emi_interest: Decimal | None = None
    unsplit_emi: bool = False


def month_bounds(year: int, month: int) -> tuple[date, date]:
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def add_months(d: date, n: int) -> date:
    idx = d.year * 12 + (d.month - 1) + n
    y, m = divmod(idx, 12)
    return date(y, m + 1, min(d.day, calendar.monthrange(y, m + 1)[1]))


# ---------------------------------------------------------------------------------- loans / EMI
def emi_amount(principal: Decimal, annual_rate_pct: Decimal, months: int) -> Decimal:
    """Standard reducing-balance EMI: P*r*(1+r)^n / ((1+r)^n - 1), r = annual/1200. Rounded half-up to paise."""
    if principal <= 0 or months <= 0 or annual_rate_pct < 0:
        raise ValueError("invalid loan terms")
    if annual_rate_pct == 0:
        return q(principal / months)
    r = annual_rate_pct / Decimal(1200)
    f = (1 + r) ** months
    return q(principal * r * f / (f - 1))


@dataclass(frozen=True)
class Installment:
    number: int
    due: date
    emi: Decimal
    interest: Decimal
    principal: Decimal
    balance: Decimal


def amortization(
    principal: Decimal,
    annual_rate_pct: Decimal,
    months: int,
    start: date,
    emi: Decimal | None = None,
) -> list[Installment]:
    """Schedule where interest each month = balance*r rounded to paise; the LAST instalment is adjusted so the
    balance closes at exactly 0.00 (rounding residue goes there, and only there)."""
    pay = emi if emi is not None else emi_amount(principal, annual_rate_pct, months)
    r = annual_rate_pct / Decimal(1200)
    bal, out = principal, []
    for n in range(1, months + 1):
        interest = q(bal * r)
        if n == months:
            prin = bal
            pay_n = q(prin + interest)
        else:
            prin = q(pay - interest)
            pay_n = pay
        bal = q(bal - prin)
        out.append(Installment(n, add_months(start, n), pay_n, interest, prin, bal))
    return out


def loan_position(schedule: Sequence[Installment], today: date) -> dict[str, object]:
    """Installments due on/before `today` are treated as paid per schedule (no payment tracking claim is made)."""
    paid = [i for i in schedule if i.due <= today]
    nxt = next((i for i in schedule if i.due > today), None)
    outstanding = (
        paid[-1].balance
        if paid
        else (schedule[0].balance + schedule[0].principal if schedule else ZERO)
    )
    return {
        "installments_total": len(schedule),
        "installments_paid_per_schedule": len(paid),
        "outstanding_principal": outstanding,
        "interest_paid_per_schedule": sum((i.interest for i in paid), ZERO),
        "total_interest": sum((i.interest for i in schedule), ZERO),
        "next_due": nxt.due if nxt else None,
        "next_emi": nxt.emi if nxt else None,
    }


# ---------------------------------------------------------------------------------- summaries
def _counted(txns: Iterable[TxnView]) -> list[TxnView]:
    return [t for t in txns if t.kind != "transfer"]


def recognised_debit(t: TxnView) -> Decimal:
    """The part of a debit that is recognised EXPENSE (cash outflow is always the full amount)."""
    if t.unsplit_emi:
        return ZERO
    return t.emi_interest if t.emi_interest is not None else t.amount


def summarise(txns: Iterable[TxnView], start: date, end: date) -> dict[str, object]:
    """Income, expense, net and savings rate for [start, end]. Credits of kind 'refund' REDUCE expense in their
    category instead of counting as income; transfers are excluded entirely."""
    income = expense = ZERO
    by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    income_by_cat: dict[str, Decimal] = defaultdict(lambda: ZERO)
    for t in _counted(txns):
        if not start <= t.txn_date <= end:
            continue
        if t.direction == "debit":
            amt = recognised_debit(t)
            expense += amt
            by_cat[t.category] += amt
        elif t.kind == "refund":
            expense -= t.amount
            by_cat[t.category if t.category != "refunds" else "uncategorized"] -= t.amount
        else:
            income += t.amount
            income_by_cat[t.category] += t.amount
    net = income - expense
    return {
        "start": start,
        "end": end,
        "income": q(income),
        "expense": q(expense),
        "net": q(net),
        "savings_rate_pct": q(net / income * 100) if income > 0 else None,
        "expense_by_category": sorted(
            ((c, q(v)) for c, v in by_cat.items() if v != 0), key=lambda kv: (-kv[1], kv[0])
        ),
        "income_by_category": sorted(
            ((c, q(v)) for c, v in income_by_cat.items()), key=lambda kv: (-kv[1], kv[0])
        ),
    }


def cashflow(
    txns: Iterable[TxnView], start: date, end: date, opening: Decimal = ZERO
) -> dict[str, object]:
    """Per-day net with running balance, plus a month-end projection of spend: average daily expense so far
    * days in the month (a pace estimate, labelled as such; it ignores known upcoming bills)."""
    days: dict[date, Decimal] = defaultdict(lambda: ZERO)
    spent = ZERO
    for t in _counted(txns):
        if start <= t.txn_date <= end:
            signed = t.amount if t.direction == "credit" else -t.amount
            days[t.txn_date] += signed
            if t.direction == "debit":
                spent += recognised_debit(
                    t
                )  # the pace of recognised expense; the cash series above is the full amount
            elif t.kind == "refund":
                spent -= t.amount
    series, run = [], opening
    d = start
    while d <= end:
        run += days.get(d, ZERO)
        series.append({"date": d, "net": q(days.get(d, ZERO)), "balance": q(run)})
        d += timedelta(days=1)
    elapsed = (end - start).days + 1
    month_days = calendar.monthrange(start.year, start.month)[1]
    projected = q(spent / elapsed * month_days) if start.day == 1 and elapsed > 0 else None
    return {
        "series": series,
        "closing": q(run),
        "projected_month_expense": projected,
        "projection_note": "average daily expense so far x days in month; ignores scheduled bills",
    }


def budget_status(
    spent_by_cat: dict[str, Decimal], budgets: dict[str, Decimal], total_spent: Decimal
) -> list[dict[str, object]]:
    out = []
    for cat in sorted(budgets):
        limit = budgets[cat]
        spent = total_spent if cat == "*" else spent_by_cat.get(cat, ZERO)
        pct = q(spent / limit * 100)
        out.append(
            {
                "category": cat,
                "limit": q(limit),
                "spent": q(spent),
                "remaining": q(limit - spent),
                "pct": pct,
                "status": "over" if spent > limit else ("warning" if pct >= WARN_PCT else "ok"),
            }
        )
    return out


def card_utilisation(
    opening_owed: Decimal, txns: Iterable[TxnView], limit: Decimal | None
) -> dict[str, object]:
    owed = opening_owed
    for t in txns:  # on a card: debits increase what is owed, credits (payments/refunds) reduce it
        owed += t.amount if t.direction == "debit" else -t.amount
    util = q(owed / limit * 100) if limit and limit > 0 else None
    band = (
        None
        if util is None
        else ("high" if util >= 70 else "elevated" if util >= 30 else "healthy")
    )
    return {"owed": q(owed), "limit": limit and q(limit), "utilisation_pct": util, "band": band}


# ---------------------------------------------------------------------------------- subscriptions
@dataclass(frozen=True)
class DetectedSubscription:
    merchant_hash: str
    amount: Decimal
    period: str
    last_seen: date
    next_due: date
    occurrences: int


_PERIODS = (("weekly", 7, 2), ("monthly", 30, 4), ("yearly", 365, 10))


def detect_subscriptions(txns: Iterable[TxnView], today: date) -> list[DetectedSubscription]:
    """>=3 debits to the same merchant, amounts within 5% of the median, and gaps matching a period
    (weekly 7+-2d, monthly 30+-4d, yearly 365+-10d). Detection only SUGGESTS: the user confirms."""
    groups: dict[str, list[TxnView]] = defaultdict(list)
    for t in _counted(txns):
        if t.direction == "debit" and t.merchant_hash:
            groups[t.merchant_hash].append(t)
    found = []
    for mh, items in groups.items():
        items.sort(key=lambda t: t.txn_date)
        if len(items) < 3:
            continue
        med = statistics.median(i.amount for i in items)
        if any(abs(i.amount - med) > med * Decimal("0.05") for i in items):
            continue
        gaps = [(b.txn_date - a.txn_date).days for a, b in zip(items, items[1:], strict=False)]
        for name, centre, tol in _PERIODS:
            if all(abs(g - centre) <= tol for g in gaps):
                last = items[-1].txn_date
                nxt = (
                    add_months(last, 1)
                    if name == "monthly"
                    else last + timedelta(days=centre if name == "weekly" else 365)
                )
                if (today - last).days <= centre + tol * 2:  # still active
                    found.append(
                        DetectedSubscription(mh, q(Decimal(med)), name, last, nxt, len(items))
                    )
                break
    return sorted(found, key=lambda s: s.merchant_hash)


# ---------------------------------------------------------------------------------- insights
@dataclass(frozen=True)
class Insight:
    code: str
    severity: str  # info | warning | alert
    params: dict[str, object] = field(default_factory=dict)


def insights(
    current: dict[str, object],
    previous: dict[str, object] | None,
    budgets: list[dict[str, object]],
    txns: Sequence[TxnView],
    upcoming: list[dict[str, object]],
    today: date,
) -> list[Insight]:
    out: list[Insight] = []
    exp_cat: list[tuple[str, Decimal]] = current["expense_by_category"]  # type: ignore[assignment]
    income, expense, net = (cast(Decimal, current[k]) for k in ("income", "expense", "net"))
    if exp_cat:
        top, amt = exp_cat[0]
        out.append(
            Insight(
                "top_category",
                "info",
                {
                    "category": top,
                    "amount": amt,
                    "share_pct": q(amt / expense * 100) if expense > 0 else ZERO,
                },
            )
        )
    if income > 0:
        rate = q(net / income * 100)
        if net < 0:
            out.append(Insight("spending_exceeds_income", "alert", {"deficit": q(-net)}))
        elif rate < 10:
            out.append(Insight("low_savings_rate", "warning", {"savings_rate_pct": rate}))
    if previous:
        prev = dict(previous["expense_by_category"])  # type: ignore[call-overload]
        for cat, amt in exp_cat:
            old = prev.get(cat, ZERO)
            delta = amt - old
            if old > 0 and delta >= 500 and delta / old >= Decimal("0.25"):
                out.append(
                    Insight(
                        "category_increase",
                        "warning",
                        {
                            "category": cat,
                            "previous": old,
                            "current": amt,
                            "change_pct": q(delta / old * 100),
                        },
                    )
                )
    for b in budgets:
        if b["status"] == "over":
            out.append(
                Insight(
                    "budget_exceeded",
                    "alert",
                    {"category": b["category"], "limit": b["limit"], "spent": b["spent"]},
                )
            )
        elif b["status"] == "warning":
            out.append(
                Insight(
                    "budget_near_limit", "warning", {"category": b["category"], "pct": b["pct"]}
                )
            )
    debits = [
        recognised_debit(t) for t in _counted(txns) if t.direction == "debit" and not t.unsplit_emi
    ]
    if len(debits) >= 5:
        med = statistics.median(debits)
        for t in sorted(_counted(txns), key=lambda t: -t.amount):
            if (
                t.direction == "debit"
                and not t.unsplit_emi
                and recognised_debit(t) > 3 * med
                and recognised_debit(t) >= 2000
                and t.txn_date.year == today.year
                and t.txn_date.month == today.month
            ):
                out.append(
                    Insight(
                        "unusually_large_expense",
                        "warning",
                        {
                            "date": t.txn_date,
                            "amount": recognised_debit(t),
                            "typical": q(Decimal(med)),
                        },
                    )
                )
                break
    for u in upcoming:
        out.append(Insight("upcoming_due", "info", dict(u)))
    order = {"alert": 0, "warning": 1, "info": 2}
    return sorted(out, key=lambda i: (order[i.severity], i.code))


_TEMPLATES = {
    "top_category": "Your biggest spending category is {category}: ₹{amount} ({share_pct}% of spending).",
    "spending_exceeds_income": "You spent ₹{deficit} more than you earned this period.",
    "low_savings_rate": "Your savings rate is {savings_rate_pct}%, below 10%.",
    "category_increase": "{category} spending rose {change_pct}% (₹{previous} to ₹{current}).",
    "budget_exceeded": "You are over your {category} budget: ₹{spent} against ₹{limit}.",
    "budget_near_limit": "You have used {pct}% of your {category} budget.",
    "unusually_large_expense": "A ₹{amount} expense on {date} is well above your typical ₹{typical}.",
    "upcoming_due": "{kind} of ₹{amount} is due on {due}.",
}


def render_insight(i: Insight) -> str:
    """Fixed templates filled with computed numbers: the wording never invents a figure."""
    return _TEMPLATES[i.code].format(**i.params)
