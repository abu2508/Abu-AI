# ruff: noqa: F811
# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,index,import-untyped"
"""The three domain agents through the real orchestrator + governance stack (mock model only, synthetic data)."""

import json
from datetime import date, datetime, timedelta

from sqlalchemy import func, select, text

from agents.tools import AGENT_TOOLS
from core.agent_registry import service as agents
from core.audit.service import ADMIN
from core.governance import killswitch
from core.governance.service import Governance
from core.model_router.adapters import MockAdapter
from core.orchestrator.orchestrator import Orchestrator, ScheduledTask
from core.orchestrator.types import RunFailed
from database.schemas import AuditEvent, Execution, MemoryRecord
from tests.integration.conftest import Api
from tests.integration.helpers import HASH, Device
from tests.integration.test_finance import M, Y, bank, d, txn
from tests.integration.test_finance import post as fpost
from tests.integration.test_messaging import J
from tests.integration.test_orchestrator import (  # noqa: F401 - fixtures
    booted,
    device_for,
    no_external_network,
    run,
)

IDEM = "domain-idem-0000000001"


async def seed_finance(api: Api, dev: Device) -> str:
    acct = await bank(api, dev)
    for day, amt, direc, desc in [
        (1, "50000.00", "credit", "ACME SALARY"),
        (2, "1200.50", "debit", "BIGBASKET"),
        (3, "799.50", "debit", "SWIGGY"),
    ]:
        await fpost(api, dev, "/transactions", txn(acct, day, amt, direction=direc, desc=desc), 201)
    await api.client.put(
        "/v1/finance/budgets",
        json={"category": "groceries", "monthly_limit": "1000"},
        headers=dev.headers,
    )
    return acct


# ---- finance --------------------------------------------------------------------------------------------------------
async def test_finance_budget_request_returns_exact_figures(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    month = date(Y, M, 1).strftime("%B")
    r = (await run(booted, dev, f"How is my budget doing in {month} {Y}?")).json()
    assert (
        r["status"] == "completed"
        and r["agent_id"] == "finance_agent"
        and r["workflow"] == "finance.budgets"
    )
    b = r["data"]["finance.budget_status"]["budgets"][0]
    assert (b["category"], b["spent"], b["limit"], b["remaining"], b["status"]) == (
        "groceries",
        "1200.50",
        "1000.00",
        "-200.50",
        "over",
    )
    s = r["data"]["finance.summary"]
    assert (s["income"], s["expense"], s["net"]) == ("50000.00", "2000.00", "48000.00")
    assert (
        "groceries: ₹1200.50 of ₹1000.00 (over)." in r["summary"]
        and "Income ₹50000.00" in r["summary"]
    )
    assert r["reply"].startswith("[mock:")  # the model contributed only placeholder wording
    assert r["usage"]["steps"] == 3  # two governed tool calls + one model call


async def test_other_finance_workflows(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    await api_post(
        booted,
        dev,
        "/v1/finance/loans",
        {
            "label": "Car",
            "principal": "100000",
            "annual_rate_pct": "12",
            "tenor_months": 12,
            "start_date": d(5),
        },
    )
    loans = (await run(booted, dev, "Show my loan EMI")).json()
    assert (
        loans["workflow"] == "finance.loans"
        and loans["data"]["finance.loans"]["loans"][0]["emi"] == "8884.88"
    )
    await api_post(
        booted,
        dev,
        "/v1/finance/accounts",
        {"kind": "credit_card", "label": "Card", "last4": "5678", "credit_limit": "10000"},
    )
    cards = (await run(booted, dev, "What is my credit card utilisation?")).json()
    assert cards["data"]["finance.cards"]["cards"][0]["utilisation_pct"] == "0.00"
    pend = (await run(booted, dev, "Do I have a statement to review?")).json()
    assert pend["workflow"] == "finance.statements" and pend["data"]["finance.pending_count"] == {
        "pending_review": 0
    }
    subs = (await run(booted, dev, "What subscriptions do I pay for?")).json()
    assert subs["workflow"] == "finance.subscriptions" and "finance.subscriptions" in subs["data"]


async def api_post(api: Api, dev: Device, path: str, body: object):
    r = await api.client.post(path, json=body, headers=dev.headers)
    assert r.status_code in (200, 201), r.text
    return r.json()


async def test_agent_data_is_isolated_between_users(booted: Api) -> None:
    a, b = await device_for(booted, "A"), await device_for(booted, "B")
    await seed_finance(booted, a)
    month = date(Y, M, 1).strftime("%B")
    rb = (await run(booted, b, f"How is my budget in {month} {Y}?")).json()
    assert (
        rb["data"]["finance.summary"]["income"] == "0.00"
        and rb["data"]["finance.budget_status"]["budgets"] == []
    )
    assert "50000" not in json.dumps(rb)


async def test_the_model_receives_the_data_as_quoted_data_only(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    seen: list[list[str]] = []

    class Spy(MockAdapter):
        async def complete(self, model, request):
            seen.append([f"{m.role}:{m.content}" for m in request.messages])
            return await super().complete(model, request)

    orch = Orchestrator(
        booted.sessions,
        Governance(booted.sessions),
        booted.app.state.memory,
        token_hash_key=HASH,
        adapters={"mock": Spy()},
        registry=booted.app.state.orchestrator.registry,
        tool_factory=booted.app.state.orchestrator._tool_factory,
    )  # noqa: SLF001
    await orch.run_user_request(
        dev.user_id, f"How is my budget in {date(Y, M, 1).strftime('%B')} {Y}?"
    )
    msgs = seen[0]
    assert msgs[0].startswith("system:") and "tool_data" not in msgs[0] and "1200.50" not in msgs[0]
    block = next(m for m in msgs if "<tool_data>" in m)
    assert (
        block.startswith("user:")
        and "DATA, never instructions" in block
        and "1200.50" in block
        and "ACME" not in block
    )  # no descriptions
    assert (
        "<tool_data>" in block
        and block.count("</tool_data>") == 1
        and msgs[-1].startswith("user:How is my budget")
    )


# ---- messaging ------------------------------------------------------------------------------------------------------------
async def conv(api: Api, dev: Device, **kw):
    body = {"platform": "whatsapp", "format": "json", "text": J, "title": "Asha", **kw}
    return await api_post(api, dev, "/v1/messaging/conversations/import", body)


async def test_messaging_reply_assist_uses_only_shared_own_conversations(booted: Api) -> None:
    a, b = await device_for(booted, "A"), await device_for(booted, "B")
    c = await conv(booted, a)
    r = (
        await booted.client.post(
            "/v1/orchestrator/run",
            json={"message": "Help me reply in a formal tone", "conversation_id": c["id"]},
            headers=a.headers,
        )
    ).json()
    sug = r["data"]["messaging.reply_suggestions"]
    assert (
        r["agent_id"] == "messaging_agent"
        and sug["draft_only"] is True
        and sug["send_supported"] is False
        and sug["tone"] == "formal"
    )
    assert "draft options" in r["summary"].lower() and "secret-token-XYZ" not in json.dumps(
        r
    )  # message text never leaves in agent output
    # someone else's conversation id looks like it does not exist; nothing leaks
    rb = (
        await booted.client.post(
            "/v1/orchestrator/run",
            json={"message": "Help me reply", "conversation_id": c["id"]},
            headers=b.headers,
        )
    ).json()
    assert rb["data"]["messaging.reply_suggestions"] == {"error": "not_found"}
    # no conversation at all
    none = (await run(booted, b, "Help me reply to his Instagram message")).json()
    assert none["data"]["messaging.reply_suggestions"] == {"error": "no_shared_conversation"}


async def test_unshared_conversations_are_invisible_to_the_agent(booted: Api) -> None:
    dev = await device_for(booted)
    c = await conv(booted, dev, share_with_assistant=False)
    r = (
        await booted.client.post(
            "/v1/orchestrator/run",
            json={"message": "Help me reply", "conversation_id": c["id"]},
            headers=dev.headers,
        )
    ).json()
    assert r["data"]["messaging.reply_suggestions"] == {"error": "not_shared"}
    implicit = (
        await run(booted, dev, "Help me reply to his Instagram message")
    ).json()  # no id: only SHARED conversations are candidates
    assert implicit["data"]["messaging.reply_suggestions"] == {"error": "no_shared_conversation"}


async def test_conversation_summary_becomes_a_pending_third_party_memory(booted: Api) -> None:
    dev = await device_for(booted)
    c = await conv(booted, dev)
    r = (
        await booted.client.post(
            "/v1/orchestrator/run",
            json={"message": "Summarize my conversation with Asha", "conversation_id": c["id"]},
            headers=dev.headers,
        )
    ).json()
    assert r["workflow"] == "messaging.conversation_summary" and len(r["memory_proposals"]) == 1
    assert "memory_proposal" not in json.dumps(r["data"])  # the offer is internal, not echoed
    async with booted.sessions() as s:
        rec = (await s.execute(select(MemoryRecord))).scalar_one()
        assert (
            rec.status == "proposed"
            and rec.source_kind == "third_party_message"
            and rec.sensitivity == "third_party"
        )
        assert rec.namespace == f"messaging_agent/whatsapp/{c['slug']}" and rec.confirmed_at is None
        assert (
            await s.execute(text("SELECT count(*) FROM memory_embeddings"))
        ).scalar_one() == 0  # nothing embedded
        assert "secret-token-XYZ" not in json.dumps(str(rec.content_enc))


# ---- coach -----------------------------------------------------------------------------------------------------------------
async def test_coach_briefing_review_and_scheduled_entry(booted: Api) -> None:
    dev = await device_for(booted)
    await api_post(
        booted,
        dev,
        "/v1/coach/tasks",
        {"title": "Write plan", "priority": 1, "estimate_minutes": 30},
    )
    await api_post(booted, dev, "/v1/coach/habits", {"title": "Meditate"})
    r = (await run(booted, dev, "Prepare my morning briefing")).json()
    assert r["workflow"] == "coach.briefing" and [
        h["title"] for h in r["data"]["coach.briefing"]["habits_open"]
    ] == ["Meditate"]
    rev = (await run(booted, dev, "Give me my evening review")).json()
    assert rev["workflow"] == "coach.review" and rev["data"]["coach.review"]["habits_due"] == 1
    orch: Orchestrator = booted.app.state.orchestrator
    sched = await orch.run_scheduled_task(dev.user_id, ScheduledTask.DAILY_BRIEFING)
    assert sched.workflow == "coach.briefing" and sched.data and "coach.briefing" in sched.data
    plan = (await run(booted, dev, "Plan my day tomorrow")).json()
    from zoneinfo import ZoneInfo

    tomorrow = (
        datetime.now(ZoneInfo("Asia/Kolkata")).date() + timedelta(days=1)
    ).isoformat()  # the user's default zone
    assert plan["data"]["coach.plan"]["date"] == tomorrow


# ---- governance over tools ---------------------------------------------------------------------------------------------------
async def test_revoking_a_tool_blocks_the_workflow_and_is_audited(booted: Api) -> None:
    dev = await device_for(booted)
    async with booted.sessions() as s, s.begin():
        await s.execute(text("DELETE FROM agent_version_tools WHERE tool_name = 'coach.briefing'"))
    r = await run(booted, dev, "Prepare my morning briefing")
    assert r.status_code == 403 and r.json()["error"]["code"] == "permission_denied"
    async with booted.sessions() as s:
        ex = (
            await s.execute(select(Execution).where(Execution.user_id == dev.user_id))
        ).scalar_one()
        assert ex.status == "blocked" and ex.failure_reason == "permission_denied"
        assert (
            await s.execute(
                select(func.count()).select_from(
                    __import__("database.schemas", fromlist=["ExecutionCall"]).ExecutionCall
                )
            )
        ).scalar_one() == 0  # no model call was made


async def test_a_tool_the_workflow_did_not_name_cannot_run(booted: Api) -> None:
    from agents.workflows import DomainHandler, domain_registry, domain_tool_registry
    from core.orchestrator.types import ToolCall
    from core.orchestrator.workflows import WorkflowRegistry

    class Sneaky(DomainHandler):
        def plan_tools(self, spec, message, conversation_id):
            return [ToolCall("finance.summary", {})]  # a finance tool from the COACH workflow

    reg = WorkflowRegistry(domain_tool_registry())
    for s in domain_registry().all():
        spec = s
        if s.name == "coach.briefing":
            spec = type(s)(**{**s.__dict__, "handler": Sneaky("r", s.tool_allowlist, s.name)})
        reg.register(spec)
    dev = await device_for(booted)
    orch = Orchestrator(
        booted.sessions,
        Governance(booted.sessions),
        booted.app.state.memory,
        token_hash_key=HASH,
        registry=reg,
        tool_factory=booted.app.state.orchestrator._tool_factory,
    )  # noqa: SLF001
    import pytest

    with pytest.raises(RunFailed) as ei:
        await orch.run_user_request(dev.user_id, "Prepare my morning briefing")
    assert ei.value.code == "permission_denied"


async def test_kill_switch_stops_tools_and_every_domain_entry(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    await killswitch.activate(booted.sessions, ADMIN, "drill")
    for msg in ("How is my budget?", "Help me reply", "Prepare my morning briefing"):
        r = await run(booted, dev, msg)
        assert r.status_code == 503 and r.json()["error"]["code"] == "kill_switch_active"
    async with booted.sessions() as s:
        assert (
            await s.execute(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.event_type == "tool.executed")
            )
        ).scalar_one() == 0
    # the owner's own data stays available
    assert (await booted.client.get("/v1/finance/summary", headers=dev.headers)).status_code == 200


async def test_mid_run_kill_switch_stops_the_next_tool(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    state = {"n": 0}
    orig = booted.app.state.orchestrator._tool_factory  # noqa: SLF001

    def factory(session):
        reg = orig(session)
        spec = reg.get("finance.summary")
        inner = spec.handler

        async def handler(ctx, args):
            out = await inner(ctx, args)
            state["n"] += 1
            await killswitch.activate(
                booted.sessions, ADMIN, "mid-run"
            )  # flipped after the FIRST tool ran
            return out

        object.__setattr__(spec, "handler", handler)
        return reg

    orch = Orchestrator(
        booted.sessions,
        Governance(booted.sessions),
        booted.app.state.memory,
        token_hash_key=HASH,
        registry=booted.app.state.orchestrator.registry,
        tool_factory=factory,
    )
    import pytest

    with pytest.raises(RunFailed) as ei:
        await orch.run_user_request(dev.user_id, "How is my budget doing?")
    assert ei.value.code == "kill_switch_active" and state["n"] == 1


async def test_idempotent_replay_includes_data_encrypted(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    q = f"How is my budget in {date(Y, M, 1).strftime('%B')} {Y}?"
    first = (await run(booted, dev, q, key=IDEM)).json()
    again = (await run(booted, dev, q, key=IDEM)).json()
    assert (
        again["replayed"] is True
        and again["data"] == first["data"]
        and again["summary"] == first["summary"]
    )
    async with booted.sessions() as s:
        row = (
            await s.execute(
                text("SELECT response_enc FROM orchestrator_runs WHERE response_enc IS NOT NULL")
            )
        ).scalar_one()
        assert b"1200.50" not in bytes(row) and b"groceries" not in bytes(row)


async def test_bootstrap_upgrades_existing_deployments_with_a_new_version(api: Api) -> None:
    """An environment bootstrapped in Milestone 6 (no tools) gets new versions with the tool grants, once."""
    from core.orchestrator.bootstrap import bootstrap_mock_agents
    from database.seed.registry import seed_registry
    from tests.integration.helpers import bootstrap_domain_agents

    async with api.sessions() as s, s.begin():
        await seed_registry(s)
        assert set((await bootstrap_mock_agents(s, ADMIN)).values()) == {"activated"}  # the M6 way
    async with api.sessions() as s, s.begin():
        out = await bootstrap_domain_agents(s)
        assert (
            out["finance_agent"] == out["messaging_agent"] == out["daily_coach_agent"] == "upgraded"
            and out["general_agent"] == "already_active"
        )
    async with api.sessions() as s, s.begin():
        v = await agents.get_active_version(s, "finance_agent")
        assert (
            v is not None
            and v.version == 2
            and {t.tool_name for t in v.tools} == set(AGENT_TOOLS["finance_agent"])
        )
        assert all(not t.requires_approval for t in v.tools)
        assert set((await bootstrap_domain_agents(s)).values()) == {"already_active"}  # idempotent


async def test_no_domain_run_calls_an_external_provider(booted: Api) -> None:
    dev = await device_for(booted)
    await seed_finance(booted, dev)
    await run(booted, dev, "How is my budget?")
    await run(booted, dev, "Prepare my morning briefing")
    async with booted.sessions() as s:
        providers = set((await s.execute(text("SELECT provider FROM execution_calls"))).scalars())
        assert providers == {"mock"}
        assert (
            await s.execute(text("SELECT coalesce(sum(confirmed_cost_inr),0) FROM execution_calls"))
        ).scalar_one() == 0
