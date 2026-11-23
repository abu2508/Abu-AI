"""The READ-ONLY domain tools an agent may be granted (code-defined: names and input schemas live here).

Every tool: has a strict input model (extra=forbid, no user id - the user comes from the governed RunContext),
is risk READ (no side effects), returns JSON-safe plain data (money as strings), and returns NO raw
third-party message text. They run only through ToolExecutor (kill switch, per-version grant, schema
validation, step/time limits, audit). There is no tool that writes, pays, sends or contacts anyone.
"""

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.ext.asyncio import AsyncSession

from core.permissions.runtime import RunContext
from core.permissions.tools import ToolRegistry, ToolRisk, ToolSpec
from domain.coach.service import CoachService
from domain.common.crypto import FieldCipher
from domain.common.jsonable import jsonable
from domain.finance import analytics as fin_an
from domain.finance.service import FinanceService
from domain.messaging import analysis as msg_an
from domain.messaging.service import MessagingError, MessagingService


class _In(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NoArgs(_In):
    pass


class YearMonth(_In):
    year: int | None = Field(default=None, ge=2000, le=2100)
    month: int | None = Field(default=None, ge=1, le=12)


class Conversation(_In):
    conversation_id: uuid.UUID | None = None  # None = the most recent conversation the user shared
    tone: Literal["casual", "warm", "formal", "brief"] | None = None


class Day(_In):
    day: date | None = None
    offset_days: int | None = Field(
        default=None, ge=0, le=30
    )  # 1 = tomorrow (resolved in the user's own timezone)


@dataclass
class ToolDeps:
    session: AsyncSession
    cipher: FieldCipher


Handler = Callable[[RunContext, Any], Awaitable[Any]]

# name -> (description, input model, group)
SPECS: dict[str, tuple[str, type[_In]]] = {
    "finance.summary": (
        "Income, expense, net and category totals for a month (confirmed transactions only).",
        YearMonth,
    ),
    "finance.cashflow": ("Daily cash flow and a month-end spend projection.", YearMonth),
    "finance.budget_status": ("Budget limits versus spending for a month.", YearMonth),
    "finance.insights": ("Rule-based insights computed from exact figures.", YearMonth),
    "finance.upcoming": ("EMIs and subscriptions due in the next two weeks.", NoArgs),
    "finance.loans": ("Loans with outstanding principal and next EMI.", NoArgs),
    "finance.cards": ("Credit-card balances and utilisation.", NoArgs),
    "finance.subscriptions": ("Active and suggested subscriptions.", NoArgs),
    "finance.pending_count": ("How many transactions await the user's review.", NoArgs),
    "messaging.analyse": (
        "Statistics about one shared conversation (no message text).",
        Conversation,
    ),
    "messaging.reply_suggestions": (
        "Draft-only reply options for one shared conversation.",
        Conversation,
    ),
    "messaging.summary": (
        "A content-free summary suitable for a pending memory proposal.",
        Conversation,
    ),
    "coach.plan": ("The time-blocked plan for a day.", Day),
    "coach.briefing": ("The morning briefing.", NoArgs),
    "coach.review": ("The evening review.", NoArgs),
    "coach.progress": ("Task, habit and goal progress.", NoArgs),
    "coach.habits": ("Habits with streaks.", NoArgs),
    "coach.goals": ("Goals with on-track / at-risk status.", NoArgs),
}


def _handlers(d: ToolDeps) -> dict[str, Handler]:
    fin = FinanceService(d.session, d.cipher)
    msg = MessagingService(d.session, d.cipher)
    coach = CoachService(d.session, d.cipher)

    def ym(a: YearMonth) -> tuple[int, int]:
        t = fin.today()
        return a.year or t.year, a.month or t.month

    async def conv_id(ctx: RunContext, a: Conversation) -> uuid.UUID | None:
        if a.conversation_id:
            return a.conversation_id
        shared = await msg.conversations(ctx.user_id, shared_only=True)
        return shared[0].id if shared else None

    async def safe(
        ctx: RunContext, a: Conversation, fn: Callable[[uuid.UUID], Awaitable[Any]]
    ) -> Any:
        cid = await conv_id(ctx, a)
        if cid is None:
            return {"error": "no_shared_conversation"}
        try:
            return await fn(cid)
        except MessagingError as exc:  # not found / not shared: a result, not a crash
            return {"error": exc.code}

    async def summary(ctx: RunContext, a: YearMonth) -> Any:
        return await fin.summary(ctx.user_id, *ym(a))

    async def cashflow(ctx: RunContext, a: YearMonth) -> Any:
        return await fin.cashflow(ctx.user_id, *ym(a))

    async def budgets(ctx: RunContext, a: YearMonth) -> Any:
        return {"budgets": await fin.budget_report(ctx.user_id, *ym(a))}

    async def insights(ctx: RunContext, a: YearMonth) -> Any:
        items = await fin.insights(ctx.user_id, *ym(a))
        return {
            "insights": [
                {
                    "code": i.code,
                    "severity": i.severity,
                    "params": i.params,
                    "text": fin_an.render_insight(i),
                }
                for i in items
            ]
        }

    async def upcoming(ctx: RunContext, a: NoArgs) -> Any:
        return {"upcoming": await fin.upcoming(ctx.user_id, 14)}

    async def loans(ctx: RunContext, a: NoArgs) -> Any:
        return {"loans": [fin.loan_summary(x) for x in await fin.loans(ctx.user_id)]}

    async def cards(ctx: RunContext, a: NoArgs) -> Any:
        return {"cards": await fin.card_report(ctx.user_id)}

    async def subscriptions(ctx: RunContext, a: NoArgs) -> Any:
        subs = await fin.subscriptions(ctx.user_id)
        return {
            "active": [
                {
                    "label": d.cipher.open(x.user_id, x.id, "fin.sub.label", x.label_enc),
                    "amount": x.amount,
                    "period": x.period,
                    "next_due": x.next_due,
                }
                for x in subs
            ],
            "suggested": [
                {
                    "amount": s.amount,
                    "period": s.period,
                    "next_due": s.next_due,
                    "occurrences": s.occurrences,
                }
                for s in await fin.detected_subscriptions(ctx.user_id)
            ],
        }

    async def pending(ctx: RunContext, a: NoArgs) -> Any:
        return {"pending_review": await fin.pending_count(ctx.user_id)}

    async def analyse(ctx: RunContext, a: Conversation) -> Any:
        return await safe(ctx, a, lambda cid: msg.analysis(ctx.user_id, cid, for_agent=True))

    async def suggestions(ctx: RunContext, a: Conversation) -> Any:
        return await safe(
            ctx, a, lambda cid: msg.suggestions(ctx.user_id, cid, tone=a.tone, for_agent=True)
        )

    async def conv_summary(ctx: RunContext, a: Conversation) -> Any:
        async def go(cid: uuid.UUID) -> Any:
            analysis = await msg.analysis(ctx.user_id, cid, for_agent=True)
            conv = await msg.conversation(ctx.user_id, cid)
            return {
                "summary": msg_an.conversation_summary(analysis),
                "memory_proposal": {
                    "platform": conv.platform,
                    "conversation_slug": conv.slug,
                    "summary": msg_an.conversation_summary(analysis),
                },
            }

        return await safe(ctx, a, go)

    async def plan(ctx: RunContext, a: Day) -> Any:
        day = a.day
        if day is None and a.offset_days:
            day = (await coach.today(ctx.user_id)) + timedelta(days=a.offset_days)
        return await coach.plan(ctx.user_id, day)

    async def briefing(ctx: RunContext, a: NoArgs) -> Any:
        return await coach.briefing(ctx.user_id)

    async def review(ctx: RunContext, a: NoArgs) -> Any:
        return await coach.review(ctx.user_id)

    async def progress(ctx: RunContext, a: NoArgs) -> Any:
        return await coach.progress(ctx.user_id)

    async def habits(ctx: RunContext, a: NoArgs) -> Any:
        return {
            "habits": [
                await coach.habit_view(ctx.user_id, h.id) for h in await coach.habits(ctx.user_id)
            ]
        }

    async def goals(ctx: RunContext, a: NoArgs) -> Any:
        today = await coach.today(ctx.user_id)
        return {"goals": [coach.goal_view(g, today) for g in await coach.goals(ctx.user_id)]}

    return {
        "finance.summary": summary,
        "finance.cashflow": cashflow,
        "finance.budget_status": budgets,
        "finance.insights": insights,
        "finance.upcoming": upcoming,
        "finance.loans": loans,
        "finance.cards": cards,
        "finance.subscriptions": subscriptions,
        "finance.pending_count": pending,
        "messaging.analyse": analyse,
        "messaging.reply_suggestions": suggestions,
        "messaging.summary": conv_summary,
        "coach.plan": plan,
        "coach.briefing": briefing,
        "coach.review": review,
        "coach.progress": progress,
        "coach.habits": habits,
        "coach.goals": goals,
    }


def build_registry(deps: ToolDeps | None = None) -> ToolRegistry:
    """With `deps=None` the registry is for NAMES and SCHEMAS only (operator grants, workflow validation);
    calling a handler then fails closed."""
    handlers = _handlers(deps) if deps is not None else {}
    reg = ToolRegistry()
    for name, (desc, model) in SPECS.items():

        async def unavailable(ctx: RunContext, args: Any) -> Any:
            raise RuntimeError("tool runtime not available")

        wrapped = handlers.get(name, unavailable)

        async def run(ctx: RunContext, args: Any, _h: Handler = wrapped) -> Any:
            return jsonable(await _h(ctx, args))

        reg.register(ToolSpec(name, desc, model, ToolRisk.READ, run))
    return reg


# Which agent may be granted which tools (used by the operator bootstrap; the grant itself is still explicit).
AGENT_TOOLS: dict[str, tuple[str, ...]] = {
    "finance_agent": tuple(n for n in SPECS if n.startswith("finance.")),
    "messaging_agent": tuple(n for n in SPECS if n.startswith("messaging.")),
    "daily_coach_agent": tuple(n for n in SPECS if n.startswith("coach.")),
    "general_agent": (),
}
