"""Deterministic SYNTHETIC scenario used for the B3 golden responses and the live conformance tests.

Everything here is invented (names end in "(demo)", round amounts, content-free message templates). Time is frozen
by patching the service clocks in the TEST process only; no backend source is touched. Because this module only
drives the public HTTP API of the unchanged backend, running it before and after B3 must give identical bytes.
"""

import json
import re
import uuid
from datetime import UTC, date, datetime
from typing import Any

import pytest

from core.audit.service import ADMIN
from core.governance import killswitch
from domain.coach.service import CoachService
from domain.finance.service import FinanceService
from domain.messaging.service import MessagingService
from tests.integration.conftest import Api
from tests.integration.helpers import Device

FROZEN_DATE = date(2026, 11, 24)
FROZEN_NOW = datetime(2026, 11, 24, 6, 30, tzinfo=UTC)  # 12:00 in the default Asia/Kolkata zone

UUID_RX = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
# Fields whose values are random per run (not part of the contract's content).
RANDOM_KEYS = {"trace_id"}


def freeze_clock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(FinanceService, "today", lambda self: FROZEN_DATE)
    monkeypatch.setattr(CoachService, "now", lambda self: FROZEN_NOW)
    monkeypatch.setattr(MessagingService, "now", lambda self: FROZEN_NOW)


class Normalizer:
    """Replaces random identifiers by stable placeholders in first-appearance order."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def __call__(self, v: Any) -> Any:
        if isinstance(v, dict):
            return {k: ("<random>" if k in RANDOM_KEYS and x else self(x)) for k, x in v.items()}
        if isinstance(v, list):
            return [self(x) for x in v]
        if isinstance(v, str):
            return UUID_RX.sub(
                lambda m: self.ids.setdefault(m.group(0), f"<uuid-{len(self.ids) + 1}>"), v
            )
        return v


class Recorder:
    def __init__(self) -> None:
        self.norm = Normalizer()
        self.items: dict[str, Any] = {}

    def add(self, name: str, status: int, headers: Any, body: Any) -> None:
        assert name not in self.items, name
        keep = {
            k: headers[k]
            for k in ("content-type", "retry-after", "www-authenticate")
            if k in headers
        }
        self.items[name] = {"status": status, "headers": keep, "body": self.norm(body)}


def _body(r: Any) -> Any:
    try:
        return r.json()
    except ValueError:
        return {"_non_json": r.text}


async def call(
    api: Api, rec: Recorder, name: str, method: str, path: str, dev: Device | None, **kw: Any
) -> Any:
    headers = dict(dev.headers) if dev else {}
    headers.update(kw.pop("headers", {}))
    r = await api.client.request(method, path, headers=headers, **kw)
    rec.add(name, r.status_code, r.headers, _body(r))
    return r


async def seed(
    api: Api, dev: Device, path: str, body: object, expect: tuple[int, ...] = (200, 201)
) -> dict[str, Any]:
    r = await api.client.post(path, json=body, headers=dev.headers)
    assert r.status_code in expect, (path, r.status_code, r.text)
    return r.json() if r.content else {}


async def seed_finance(api: Api, dev: Device) -> str:
    bank = (
        await seed(
            api,
            dev,
            "/v1/finance/accounts",
            {
                "kind": "bank",
                "label": "Demo salary account (demo)",
                "institution": "hdfc",
                "last4": "1234",
            },
        )
    )["id"]
    card = (
        await seed(
            api,
            dev,
            "/v1/finance/accounts",
            {
                "kind": "credit_card",
                "label": "Demo card (demo)",
                "last4": "5678",
                "credit_limit": "10000",
            },
        )
    )["id"]

    def t(acct: str, day: str, amount: str, direction: str, desc: str) -> dict[str, object]:
        return {
            "account_id": acct,
            "date": day,
            "amount": amount,
            "direction": direction,
            "description": desc,
        }

    rows = [
        (bank, "2026-10-01", "50000.00", "credit", "ACME SALARY"),
        (bank, "2026-10-03", "18000.00", "debit", "RENT TRANSFER"),
        (bank, "2026-10-08", "1000.00", "debit", "BIGBASKET"),
        (bank, "2026-11-01", "50000.00", "credit", "ACME SALARY"),
        (bank, "2026-11-03", "18000.00", "debit", "RENT TRANSFER"),
        (bank, "2026-11-05", "1200.50", "debit", "BIGBASKET"),
        (bank, "2026-11-06", "799.50", "debit", "SWIGGY"),
        (bank, "2026-11-07", "300.00", "debit", "SWIGGY"),
        (bank, "2026-11-08", "320.00", "debit", "BIGBASKET"),
        (bank, "2026-11-09", "280.00", "debit", "SWIGGY"),
        (bank, "2026-11-10", "310.00", "debit", "BIGBASKET"),
        (bank, "2026-11-12", "12000.00", "debit", "FURNITURE STORE"),
        (card, "2026-11-15", "3000.00", "debit", "SWIGGY"),
    ]
    for a, day, amount, direction, desc in rows:
        await seed(api, dev, "/v1/finance/transactions", t(a, day, amount, direction, desc))
    r = await api.client.put(
        "/v1/finance/budgets",
        json={"category": "groceries", "monthly_limit": "1000"},
        headers=dev.headers,
    )
    assert r.status_code == 200, r.text
    await seed(
        api,
        dev,
        "/v1/finance/subscriptions",
        {
            "label": "Demo streaming (demo)",
            "amount": "499.00",
            "period": "monthly",
            "next_due": "2026-11-30",
        },
    )
    await seed(
        api,
        dev,
        "/v1/finance/loans",
        {
            "label": "Demo car loan (demo)",
            "principal": "100000",
            "annual_rate_pct": "12",
            "tenor_months": 12,
            "start_date": "2026-10-05",
        },
    )
    return str(bank)


async def seed_coach(api: Api, dev: Device) -> None:
    goal = await seed(
        api,
        dev,
        "/v1/coach/goals",
        {"title": "Run a demo 10k (demo)", "category": "health", "target_date": "2099-01-01"},
    )
    await seed(
        api, dev, "/v1/coach/goals", {"title": "Read demo books (demo)", "category": "learning"}
    )
    await seed(
        api,
        dev,
        "/v1/coach/tasks",
        {
            "title": "Write demo report (demo)",
            "priority": 1,
            "estimate_minutes": 60,
            "due_date": "2026-11-24",
            "goal_id": goal["id"],
        },
    )
    await seed(
        api,
        dev,
        "/v1/coach/tasks",
        {"title": "Review demo budget (demo)", "priority": 2, "estimate_minutes": 30},
    )
    await seed(
        api,
        dev,
        "/v1/coach/tasks",
        {
            "title": "Reply to demo emails (demo)",
            "priority": 3,
            "estimate_minutes": 20,
            "due_date": "2026-11-23",
        },
    )
    daily = await seed(
        api, dev, "/v1/coach/habits", {"title": "Meditate (demo)", "cadence": "daily"}
    )
    weekly = await seed(
        api,
        dev,
        "/v1/coach/habits",
        {"title": "Long walk (demo)", "cadence": "weekly", "target_per_week": 3},
    )
    for day in ("2026-11-21", "2026-11-22", "2026-11-23"):
        await seed(api, dev, f"/v1/coach/habits/{daily['id']}/log", {"date": day, "done": True})
    await seed(
        api, dev, f"/v1/coach/habits/{weekly['id']}/log", {"date": "2026-11-22", "done": True}
    )
    await seed(
        api,
        dev,
        "/v1/coach/events",
        {
            "title": "Demo stand-up (demo)",
            "start": "2026-11-24T14:00:00+05:30",
            "end": "2026-11-24T15:00:00+05:30",
            "reminder_minutes": 10,
        },
    )
    await seed(
        api,
        dev,
        "/v1/coach/reminders",
        {"text": "Demo reminder (demo)", "remind_at": "2026-11-24T05:00:00Z"},
    )
    await seed(api, dev, "/v1/coach/checkins", {"kind": "morning", "mood": 4, "energy": 3})


MESSAGES = [
    {
        "sender": "them",
        "text": "Hey! Are you free tomorrow evening for dinner?",
        "sent_at": "2026-11-22T09:00:00+00:00",
    },
    {"sender": "me", "text": "Maybe, let me check.", "sent_at": "2026-11-22T09:10:00+00:00"},
    {
        "sender": "them",
        "text": "Could you let me know today? Thanks!",
        "sent_at": "2026-11-23T08:00:00+00:00",
    },
]


async def seed_messaging(api: Api, dev: Device, **kw: object) -> dict[str, Any]:
    body = {
        "platform": "whatsapp",
        "format": "json",
        "text": json.dumps(MESSAGES),
        "title": "Asha (demo)",
        **kw,
    }
    return await seed(api, dev, "/v1/messaging/conversations/import", body)


# (name, message) pairs: every workflow family; "{conv}" runs also pass conversation_id.
FINANCE_RUNS = [
    ("run.finance.overview", "Show my income and savings this month"),
    ("run.finance.budgets", "How is my budget this month?"),
    ("run.finance.budgets.october", "How is my budget doing in October 2026?"),
    ("run.finance.transactions", "Give me insights on my spending this month"),
    ("run.finance.loans", "Show my loan EMI"),
    ("run.finance.cards", "What is my credit card utilisation?"),
    ("run.finance.statements", "Do I have a statement to review?"),
    ("run.finance.subscriptions", "What subscriptions do I pay for?"),
]
COACH_RUNS = [
    ("run.coach.briefing", "Prepare my morning briefing"),
    ("run.coach.review", "Give me my evening review"),
    ("run.coach.goals", "How are my goals going?"),
    ("run.coach.habits", "How are my habits and streaks?"),
    (
        "run.coach.plan",
        "Plan my day",
    ),  # F1: the orchestrator summary step fails when the plan has blocks
]


async def build(api: Api) -> dict[str, Any]:
    """Runs the whole scenario against a booted app and returns the recorded, normalised responses."""
    from tests.integration.test_orchestrator import device_for

    rec = Recorder()
    a = await device_for(api, "Golden A (demo)")
    b = await device_for(api, "Golden B (demo)")
    bank_id = await seed_finance(api, a)
    await seed_coach(api, a)
    conv = await seed_messaging(api, a)
    unshared = await seed_messaging(api, a, title="Private (demo)", share_with_assistant=False)

    # ---- the four routes B3 documents, populated and empty ---------------------------------------------------
    for who, dev in (("a", a), ("b", b)):
        await call(api, rec, f"route.{who}.finance.summary", "GET", "/v1/finance/summary", dev)
        await call(
            api,
            rec,
            f"route.{who}.finance.summary.oct",
            "GET",
            "/v1/finance/summary",
            dev,
            params={"year": 2026, "month": 10},
        )
        await call(api, rec, f"route.{who}.finance.insights", "GET", "/v1/finance/insights", dev)
        await call(api, rec, f"route.{who}.finance.upcoming", "GET", "/v1/finance/upcoming", dev)
        await call(api, rec, f"route.{who}.coach.briefing", "GET", "/v1/coach/briefing", dev)
    await call(
        api,
        rec,
        "route.a.finance.upcoming.90",
        "GET",
        "/v1/finance/upcoming",
        a,
        params={"days": 90},
    )

    # ---- orchestrator: every workflow family -----------------------------------------------------------------
    for dev_name, dev in (("a", a), ("b", b)):
        for name, msg in [*FINANCE_RUNS, *COACH_RUNS]:
            await call(
                api,
                rec,
                f"{name}.{dev_name}",
                "POST",
                "/v1/orchestrator/run",
                dev,
                json={"message": msg},
            )
    await call(
        api,
        rec,
        "run.messaging.reply_assist.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Help me reply in a formal tone", "conversation_id": conv["id"]},
    )
    await call(
        api,
        rec,
        "run.messaging.summary.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Summarize my conversation with Asha", "conversation_id": conv["id"]},
    )
    await call(
        api,
        rec,
        "run.messaging.not_shared.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Help me reply", "conversation_id": unshared["id"]},
    )
    await call(
        api,
        rec,
        "run.messaging.no_conversation.b",
        "POST",
        "/v1/orchestrator/run",
        b,
        json={"message": "Help me reply to his Instagram message"},
    )
    await call(
        api,
        rec,
        "run.messaging.not_found.b",
        "POST",
        "/v1/orchestrator/run",
        b,
        json={"message": "Help me reply", "conversation_id": conv["id"]},
    )
    await call(
        api,
        rec,
        "run.general.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Tell me about yourself please today"},
    )
    await call(
        api,
        rec,
        "run.unsupported.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Please transfer 5000 rupees to my friend"},
    )
    await call(
        api,
        rec,
        "run.clarify.a",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "How much did I spend in total this month?"},
    )

    # ---- idempotency ------------------------------------------------------------------------------------------
    key = "golden-idem-0000000001"
    h = {"Idempotency-Key": key}
    await call(
        api,
        rec,
        "idem.first",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Show my loan EMI"},
        headers=h,
    )
    await call(
        api,
        rec,
        "idem.replay",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "Show my loan EMI"},
        headers=h,
    )
    await call(
        api,
        rec,
        "idem.conflict",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "What subscriptions do I pay for?"},
        headers=h,
    )

    # ---- error envelopes --------------------------------------------------------------------------------------
    await call(api, rec, "err.401.no_token", "GET", "/v1/finance/summary", None)
    await call(
        api,
        rec,
        "err.401.garbled",
        "GET",
        "/v1/coach/briefing",
        None,
        headers={"Authorization": "Bearer not-a-token"},
    )
    await call(
        api,
        rec,
        "err.401.orchestrator",
        "POST",
        "/v1/orchestrator/run",
        None,
        json={"message": "hi there"},
    )
    await call(
        api, rec, "err.422.validation.month", "GET", "/v1/finance/summary", a, params={"month": 13}
    )
    await call(
        api, rec, "err.422.validation.run", "POST", "/v1/orchestrator/run", a, json={"message": ""}
    )
    await call(
        api,
        rec,
        "err.422.validation.extra",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "hi there", "user_id": "x"},
    )
    await call(
        api,
        rec,
        "err.422.invalid_request",
        "POST",
        "/v1/orchestrator/run",
        a,
        json={"message": "   "},
    )
    await call(
        api,
        rec,
        "err.404.coach",
        "POST",
        f"/v1/coach/tasks/{uuid.UUID(int=1)}/complete",
        a,
        json={},
    )
    await call(
        api,
        rec,
        "err.422.finance.category",
        "POST",
        "/v1/finance/transactions",
        a,
        json={
            "account_id": bank_id,
            "date": "2026-11-01",
            "amount": "1.00",
            "direction": "debit",
            "description": "X",
            "category": "not-a-category",
        },
    )
    await call(
        api,
        rec,
        "err.422.finance.credit_limit",
        "POST",
        "/v1/finance/accounts",
        a,
        json={"kind": "credit_card", "label": "No limit (demo)"},
    )
    await call(
        api,
        rec,
        "err.404.finance.account",
        "POST",
        "/v1/finance/transactions",
        a,
        json={
            "account_id": str(uuid.UUID(int=2)),
            "date": "2026-11-01",
            "amount": "1.00",
            "direction": "debit",
            "description": "X",
        },
    )
    api.app.state.memory, saved = (
        None,
        api.app.state.memory,
    )  # a domain route without memory runtime: 503 detail envelope
    try:
        await call(api, rec, "err.503.service_unavailable", "GET", "/v1/finance/summary", a)
    finally:
        api.app.state.memory = saved

    # ---- kill switch: the orchestrator stops, the user's own data stays readable ------------------------------
    await killswitch.activate(api.sessions, ADMIN, "golden drill")
    try:
        await call(
            api,
            rec,
            "killswitch.run",
            "POST",
            "/v1/orchestrator/run",
            a,
            json={"message": "Show my loan EMI"},
        )
        await call(api, rec, "killswitch.finance.summary", "GET", "/v1/finance/summary", a)
        await call(api, rec, "killswitch.coach.briefing", "GET", "/v1/coach/briefing", a)
    finally:
        await killswitch.deactivate(api.sessions, ADMIN, "golden drill over")
    return rec.items
