"""Domain workflows for the three agents. Each workflow = a bounded WorkflowSpec + a handler that (a) PLANS which
READ tools to run from the message with deterministic rules, (b) builds the model prompt, (c) renders a templated
summary from the tool results. The model never chooses a tool, a user, a conversation or a number."""

import re
import uuid
from datetime import date
from typing import Any

from agents.tools import AGENT_TOOLS, build_registry
from core.memory import namespaces as ns_rules
from core.orchestrator.types import Intent, ToolCall, WorkflowSpec
from core.orchestrator.workflows import (
    FRONT_DESK_AGENT,
    MOCK_MODEL_ALIAS,
    MockWorkflowHandler,
    WorkflowRegistry,
)
from core.permissions.tools import ToolRegistry

_MONTHS = {
    m: i
    for i, m in enumerate(
        [
            "january",
            "february",
            "march",
            "april",
            "may",
            "june",
            "july",
            "august",
            "september",
            "october",
            "november",
            "december",
        ],
        1,
    )
}
_MONTH_RX = re.compile(r"\b(" + "|".join(_MONTHS) + r")\b(?:\s+(20\d{2}))?", re.I)
_TONE_RX = re.compile(r"\b(casual|warm|formal|brief)\b", re.I)


def extract_period(message: str, today: date) -> dict[str, int]:
    """'in March', 'march 2024', 'last month' -> year/month args; otherwise {} (= this month)."""
    low = message.lower()
    if "last month" in low:
        y, m = (today.year, today.month - 1) if today.month > 1 else (today.year - 1, 12)
        return {"year": y, "month": m}
    if (hit := _MONTH_RX.search(message)) is not None:
        month = _MONTHS[hit.group(1).lower()]
        year = (
            int(hit.group(2))
            if hit.group(2)
            else (today.year if month <= today.month else today.year - 1)
        )
        return {"year": year, "month": month}
    return {}


class DomainHandler(MockWorkflowHandler):
    def __init__(self, role: str, tools: tuple[str, ...], kind: str) -> None:
        super().__init__(role)
        self._tools, self._kind = tools, kind

    def plan_tools(
        self, spec: WorkflowSpec, message: str, conversation_id: uuid.UUID | None
    ) -> list[ToolCall]:
        out: list[ToolCall] = []
        for name in self._tools:
            args: dict[str, Any] = {}
            if name in (
                "finance.summary",
                "finance.cashflow",
                "finance.budget_status",
                "finance.insights",
            ):
                args = extract_period(message, date.today())
            elif name.startswith("messaging."):
                if conversation_id:
                    args["conversation_id"] = str(conversation_id)
                if name == "messaging.reply_suggestions" and (t := _TONE_RX.search(message)):
                    args["tone"] = t.group(1).lower()
            elif name == "coach.plan":
                low = message.lower()
                args = {"offset_days": 1} if "tomorrow" in low else {}
            out.append(ToolCall(name, args))
        return out

    def summarise(self, results: dict[str, Any]) -> str | None:
        from agents.summaries import summarise

        return summarise(results)


def _spec(
    name: str,
    intent: Intent,
    agent: str,
    role: str,
    desc: str,
    tools: tuple[str, ...],
    subtype: str | None,
) -> WorkflowSpec:
    return WorkflowSpec(
        name=name,
        intent=intent,
        agent_id=agent,
        model_alias=MOCK_MODEL_ALIAS,
        handler=DomainHandler(role, tools, name),
        description=desc,
        subtype=subtype,
        memory_roots=(agent, ns_rules.USER_ROOT),
        write_roots=(agent,),
        tool_allowlist=tools,
    )


F = "finance_agent"
M = "messaging_agent"
C = "daily_coach_agent"
_FIN_ROLE = "a careful personal-finance assistant; you never move money, you quote only figures given in the data, and you state uncertainty"
_MSG_ROLE = "a reply-drafting assistant; you only DRAFT text for the user to send themselves and never quote private message text"
_COACH_ROLE = "a planning and accountability coach; you only reference the plan, habits and goals given in the data"

# (name, intent, agent, role, description, tools, subtype). The subtype=None entries keep the Milestone 6 names.
WORKFLOWS: list[tuple[str, Intent, str, str, str, tuple[str, ...], str | None]] = [
    (
        "finance.assistant.mock",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: insights and monthly summary",
        ("finance.insights", "finance.summary"),
        None,
    ),
    (
        "finance.budgets",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: budget status",
        ("finance.budget_status", "finance.summary"),
        "budgets",
    ),
    (
        "finance.subscriptions",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: subscriptions and what is due",
        ("finance.subscriptions", "finance.upcoming"),
        "subscriptions",
    ),
    (
        "finance.loans",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: loans, EMIs and what is due",
        ("finance.loans", "finance.upcoming"),
        "loans",
    ),
    (
        "finance.cards",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: credit-card utilisation",
        ("finance.cards", "finance.upcoming"),
        "cards",
    ),
    (
        "finance.statements",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: statement/alert review status",
        ("finance.pending_count", "finance.summary"),
        "statements",
    ),
    (
        "finance.transactions",
        Intent.FINANCE,
        F,
        _FIN_ROLE,
        "Finance: spending and cash flow",
        ("finance.summary", "finance.cashflow"),
        "transactions",
    ),
    (
        "messaging.reply_assist.mock",
        Intent.MESSAGING,
        M,
        _MSG_ROLE,
        "Messaging: draft replies (never sent)",
        ("messaging.reply_suggestions", "messaging.analyse"),
        None,
    ),
    (
        "messaging.conversation_summary",
        Intent.MESSAGING,
        M,
        _MSG_ROLE,
        "Messaging: conversation summary",
        ("messaging.analyse", "messaging.summary"),
        "conversation_summary",
    ),
    (
        "coach.daily_plan.mock",
        Intent.COACHING,
        C,
        _COACH_ROLE,
        "Coach: today's plan and progress",
        ("coach.plan", "coach.progress"),
        None,
    ),
    (
        "coach.briefing",
        Intent.COACHING,
        C,
        _COACH_ROLE,
        "Coach: morning briefing",
        ("coach.briefing",),
        "briefing",
    ),
    (
        "coach.review",
        Intent.COACHING,
        C,
        _COACH_ROLE,
        "Coach: evening review",
        ("coach.review",),
        "review",
    ),
    (
        "coach.goals",
        Intent.COACHING,
        C,
        _COACH_ROLE,
        "Coach: goals and progress",
        ("coach.goals", "coach.progress"),
        "goals",
    ),
    (
        "coach.habits",
        Intent.COACHING,
        C,
        _COACH_ROLE,
        "Coach: habits and streaks",
        ("coach.habits",),
        "habits",
    ),
    (
        "general.assistant.mock",
        Intent.GENERAL,
        FRONT_DESK_AGENT,
        "a general helper that explains what this assistant can do",
        "General assistant",
        (),
        None,
    ),
]


def domain_tool_registry() -> ToolRegistry:
    return build_registry(None)  # names and schemas only


def domain_registry() -> WorkflowRegistry:
    reg = WorkflowRegistry(tools=domain_tool_registry())
    for name, intent, agent, role, desc, tools, subtype in WORKFLOWS:
        if intent is Intent.GENERAL:
            reg.register(
                WorkflowSpec(
                    name=name,
                    intent=intent,
                    agent_id=agent,
                    model_alias=MOCK_MODEL_ALIAS,
                    handler=MockWorkflowHandler(role),
                    description=desc,
                    memory_roots=(agent, ns_rules.USER_ROOT),
                    write_roots=(agent,),
                )
            )
        else:
            reg.register(_spec(name, intent, agent, role, desc, tools, subtype))
    return reg


__all__ = [
    "AGENT_TOOLS",
    "DomainHandler",
    "domain_registry",
    "domain_tool_registry",
    "extract_period",
]
