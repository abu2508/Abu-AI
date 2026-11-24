"""`OrchestratorResponse.data`: `{ "<tool name>": result }`, only for the tools the chosen workflow ran.

Every value is either the tool's result model or `ToolError`. The result models below are the same JSON the tools
already produce (tool handlers wrap their output in `jsonable`); this module only describes it.
"""

from typing import Any

from pydantic import Field

from services.api.contracts.coach import (
    CoachBriefing,
    GoalsTool,
    HabitsTool,
    PlanTool,
    ProgressTool,
    ReviewTool,
)
from services.api.contracts.common import Contract
from services.api.contracts.finance import (
    BudgetsTool,
    CardsTool,
    CashflowTool,
    FinanceSummary,
    InsightsTool,
    LoansTool,
    PendingTool,
    SubscriptionsTool,
    UpcomingTool,
)
from services.api.contracts.messaging import AnalyseTool, MessagingSummaryTool, SuggestionsTool


class ToolError(Contract):
    """A tool that could not produce data: messaging `not_found` / `not_shared` / `no_shared_conversation`, or
    `result_too_large` (then the response also carries the warning `tool_data_truncated`)."""

    error: str


def _tool(name: str) -> Any:
    return Field(
        default=None, alias=name, description=f"Result of tool `{name}` (absent if it did not run)"
    )


class ToolResults(Contract):
    finance_summary: FinanceSummary | ToolError | None = _tool("finance.summary")
    finance_cashflow: CashflowTool | ToolError | None = _tool("finance.cashflow")
    finance_budget_status: BudgetsTool | ToolError | None = _tool("finance.budget_status")
    finance_insights: InsightsTool | ToolError | None = _tool("finance.insights")
    finance_upcoming: UpcomingTool | ToolError | None = _tool("finance.upcoming")
    finance_loans: LoansTool | ToolError | None = _tool("finance.loans")
    finance_cards: CardsTool | ToolError | None = _tool("finance.cards")
    finance_subscriptions: SubscriptionsTool | ToolError | None = _tool("finance.subscriptions")
    finance_pending_count: PendingTool | ToolError | None = _tool("finance.pending_count")
    messaging_analyse: AnalyseTool | ToolError | None = _tool("messaging.analyse")
    messaging_reply_suggestions: SuggestionsTool | ToolError | None = _tool(
        "messaging.reply_suggestions"
    )
    messaging_summary: MessagingSummaryTool | ToolError | None = _tool("messaging.summary")
    coach_plan: PlanTool | ToolError | None = _tool("coach.plan")
    coach_briefing: CoachBriefing | ToolError | None = _tool("coach.briefing")
    coach_review: ReviewTool | ToolError | None = _tool("coach.review")
    coach_progress: ProgressTool | ToolError | None = _tool("coach.progress")
    coach_habits: HabitsTool | ToolError | None = _tool("coach.habits")
    coach_goals: GoalsTool | ToolError | None = _tool("coach.goals")
