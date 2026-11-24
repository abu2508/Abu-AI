"""Finance response shapes. Sources: `domain/finance/analytics.py`, `domain/finance/service.py`, `agents/tools.py`."""

from typing import Literal

from pydantic import Field

from services.api.contracts.common import Contract, IsoDate, Money, UuidStr


class FinanceSummary(Contract):
    """GET /v1/finance/summary and tool `finance.summary` (identical)."""

    start: IsoDate
    end: IsoDate
    income: Money
    expense: Money
    net: Money
    savings_rate_pct: Money | None = Field(description="null when income is 0")
    expense_by_category: list[tuple[str, Money]] = Field(
        description="[category, amount] pairs, largest first; refunds can make an amount negative"
    )
    income_by_category: list[tuple[str, Money]]
    pending_review_count: int = Field(description="NOT included in the figures above")


class InsightOut(Contract):
    code: str = Field(
        description="top_category, spending_exceeds_income, low_savings_rate, category_increase, budget_exceeded, "
        "budget_near_limit, unusually_large_expense, upcoming_due. Open set."
    )
    severity: Literal["info", "warning", "alert"]
    params: dict[str, str | int | None] = Field(
        description="Figures behind `text`; keys depend on `code`. Money values are strings."
    )
    text: str = Field(description="Fixed template filled with computed numbers; safe to display")


class UpcomingItem(Contract):
    kind: Literal["subscription", "emi"]
    label: str
    amount: Money
    due: IsoDate


class InsightsTool(Contract):
    insights: list[InsightOut]


class UpcomingTool(Contract):
    upcoming: list[UpcomingItem]


class CashflowDay(Contract):
    date: IsoDate
    net: Money
    balance: Money


class CashflowTool(Contract):
    series: list[CashflowDay]
    closing: Money
    projected_month_expense: Money | None = Field(
        description="null unless the window starts on day 1 of the month"
    )
    projection_note: str


class BudgetRow(Contract):
    category: str = Field(description='"*" is the total-spending budget')
    limit: Money
    spent: Money
    remaining: Money
    pct: Money
    status: Literal["ok", "warning", "over"]


class BudgetsTool(Contract):
    budgets: list[BudgetRow]


class LoanRow(Contract):
    id: UuidStr
    label: str
    principal: Money
    annual_rate_pct: Money
    tenor_months: int
    emi: Money
    status: str
    installments_total: int
    installments_paid_per_schedule: int
    outstanding_principal: Money
    interest_paid_per_schedule: Money
    total_interest: Money
    next_due: IsoDate | None
    next_emi: Money | None


class LoansTool(Contract):
    loans: list[LoanRow]


class CardRow(Contract):
    account_id: UuidStr
    label: str
    owed: Money
    limit: Money | None
    utilisation_pct: Money | None
    band: Literal["healthy", "elevated", "high"] | None


class CardsTool(Contract):
    cards: list[CardRow]


class SubscriptionActive(Contract):
    label: str
    amount: Money
    period: str
    next_due: IsoDate


class SubscriptionSuggested(Contract):
    amount: Money
    period: str
    next_due: IsoDate
    occurrences: int


class SubscriptionsTool(Contract):
    active: list[SubscriptionActive]
    suggested: list[SubscriptionSuggested]


class PendingTool(Contract):
    pending_review: int
