# ruff: noqa: F811
# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,index,attr-defined,call-arg,union-attr"
"""B3 live conformance: real responses and real tool outputs from the (unchanged) backend, validated against the
documentation-only contracts. Synthetic data, mock model, frozen clock, no external network."""

import json
import types
from typing import Any

import httpx
import pytest
from pydantic import TypeAdapter

from agents.tools import SPECS, ToolDeps, build_registry
from domain.common.crypto import FieldCipher
from services.api.config import Settings
from services.api.contracts.coach import CoachBriefing
from services.api.contracts.errors import (
    DOMAIN_ERROR_CODES,
    ORCHESTRATOR_ERROR_CODES,
    DetailEnvelope,
    ErrorEnvelope,
    RunFailedEnvelope,
)
from services.api.contracts.finance import FinanceSummary, InsightOut, UpcomingItem
from services.api.contracts.tools import ToolResults
from services.api.main import create_app
from tests.contracts import scenario
from tests.integration.conftest import Api
from tests.integration.helpers import make_settings
from tests.integration.test_orchestrator import (  # noqa: F401 - fixtures
    booted,
    device_for,
    no_external_network,
    run,
)


def check(model: Any, response: httpx.Response) -> Any:
    return TypeAdapter(model).validate_json(response.text)


@pytest.fixture
async def world(booted: Api, monkeypatch: pytest.MonkeyPatch):
    scenario.freeze_clock(monkeypatch)
    a = await device_for(booted, "Conformance A (demo)")
    b = await device_for(booted, "Conformance B (demo)")
    await scenario.seed_finance(booted, a)
    await scenario.seed_coach(booted, a)
    conv = await scenario.seed_messaging(booted, a)
    return types.SimpleNamespace(api=booted, a=a, b=b, conv=conv)


# ---- routes: populated and empty users ----------------------------------------------------------------------------------
async def test_routes_conform_for_populated_and_empty_users(world) -> None:
    api: Api = world.api
    for dev in (world.a, world.b):
        h = dev.headers
        check(FinanceSummary, await api.client.get("/v1/finance/summary", headers=h))
        check(
            FinanceSummary,
            await api.client.get(
                "/v1/finance/summary", params={"year": 2026, "month": 10}, headers=h
            ),
        )
        check(list[InsightOut], await api.client.get("/v1/finance/insights", headers=h))
        check(list[UpcomingItem], await api.client.get("/v1/finance/upcoming", headers=h))
        check(CoachBriefing, await api.client.get("/v1/coach/briefing", headers=h))
    summary = check(
        FinanceSummary, await api.client.get("/v1/finance/summary", headers=world.b.headers)
    )
    assert (
        summary.savings_rate_pct is None and summary.expense_by_category == []
    )  # null / empty branches
    populated = check(
        CoachBriefing, await api.client.get("/v1/coach/briefing", headers=world.a.headers)
    )
    assert (
        populated.plan.blocks and populated.habits_open and populated.reminders_due
    )  # non-empty branches
    insights = check(
        list[InsightOut], await api.client.get("/v1/finance/insights", headers=world.a.headers)
    )
    assert {i.code for i in insights} >= {"budget_exceeded", "top_category", "upcoming_due"}


# ---- all 18 tools, straight from the tool registry (independent of the orchestrator's summary step) ------------------------
async def test_all_18_tool_results_conform_when_invoked_directly(world) -> None:
    api: Api = world.api
    async with api.sessions() as s:
        cipher = FieldCipher(api.app.state.memory.envelope, api.settings.token_hash_key)
        registry = build_registry(ToolDeps(s, cipher))
        ctx = types.SimpleNamespace(user_id=world.a.user_id)
        results: dict[str, Any] = {}
        for name, (_, model) in SPECS.items():
            args = (
                model(conversation_id=world.conv["id"])
                if name.startswith("messaging.")
                else model()
            )
            spec = registry.get(name)
            results[name] = await spec.handler(
                ctx, args
            )  # the registered handler: output is already JSON-safe
    assert set(results) == set(SPECS)
    # The raw handler output of messaging.summary also carries the internal `memory_proposal` offer; the orchestrator
    # strips it before it reaches `data` (and the contract describes the public shape), so check it, then drop it.
    offer = results["messaging.summary"].pop("memory_proposal")
    assert set(offer) == {"platform", "conversation_slug", "summary"}
    TypeAdapter(ToolResults).validate_json(json.dumps(results))
    plan = results["coach.plan"]
    assert plan["blocks"], (
        "the synthetic plan has scheduled blocks"
    )  # raw coach-plan output, no orchestrator involved
    assert all(isinstance(b["start"], str) for b in plan["blocks"])


async def test_unreliable_timestamps_and_tool_errors_conform(world) -> None:
    api: Api = world.api
    labelled = await scenario.seed(
        api,
        world.a,
        "/v1/messaging/conversations/import",
        {
            "platform": "whatsapp",
            "format": "labelled",
            "text": "them: Are you free tomorrow?\nme: Maybe.\nthem: Please let me know, thanks!",
            "title": "Labelled (demo)",
        },
    )
    r = await run(api, world.a, "Help me reply in a formal tone", conversation_id=labelled["id"])
    data = check(ToolResults, httpx.Response(200, text=json.dumps(r.json()["data"])))
    analyse = data.messaging_analyse
    assert (
        analyse is not None and getattr(analyse, "median_my_response_minutes", None) is None
    )  # absent when unreliable
    err = await run(api, world.b, "Help me reply", conversation_id=world.conv["id"])
    assert err.json()["data"]["messaging.reply_suggestions"] == {"error": "not_found"}
    check(ToolResults, httpx.Response(200, text=json.dumps(err.json()["data"])))


# ---- every orchestrator workflow's data ---------------------------------------------------------------------------------------
async def test_every_workflow_response_data_conforms(world) -> None:
    api: Api = world.api
    runs = [m for _, m in [*scenario.FINANCE_RUNS, *scenario.COACH_RUNS]]
    seen: set[str] = set()
    for dev in (world.a, world.b):
        for message in runs:
            r = await run(api, dev, message)
            assert r.status_code == 200, (message, r.text)
            data = r.json().get("data") or {}
            check(ToolResults, httpx.Response(200, text=json.dumps(data)))
            seen |= set(data)
    for message in ("Help me reply in a formal tone", "Summarize my conversation with Asha"):
        r = await run(api, world.a, message, conversation_id=world.conv["id"])
        data = r.json()["data"]
        check(ToolResults, httpx.Response(200, text=json.dumps(data)))
        seen |= set(data)
    assert seen == set(SPECS)


async def test_the_coach_plan_workflow_with_scheduled_blocks_completes(world) -> None:
    """F1 status: reproduced ONLY with synthetic raw datetimes passed straight to `summarise`; NOT end to end.
    Tool handlers wrap results in `jsonable`, so `summarise` always sees ISO strings. This pins that behaviour."""
    r = await run(world.api, world.a, "Plan my day")
    body = r.json()
    assert (
        r.status_code == 200
        and body["status"] == "completed"
        and body["workflow"] == "coach.daily_plan.mock"
    )
    assert body["data"]["coach.plan"]["blocks"]
    assert "12:00-13:00 Write demo report (demo)" in body["summary"]


# ---- errors: real statuses, real envelopes, documented codes --------------------------------------------------------------
async def test_error_envelopes_and_status_codes_conform(world) -> None:
    api: Api = world.api
    known = set(ORCHESTRATOR_ERROR_CODES) | set(DOMAIN_ERROR_CODES)
    checks: list[tuple[httpx.Response, int, Any]] = []
    c = api.client
    checks.append((await c.get("/v1/finance/summary"), 401, DetailEnvelope))
    checks.append(
        (
            await c.get("/v1/coach/briefing", headers={"Authorization": "Bearer junk"}),
            401,
            DetailEnvelope,
        )
    )
    checks.append((await run(api, world.a, "   "), 422, ErrorEnvelope))
    checks.append(
        (await run(api, world.a, "Show my loan EMI", key="conformance-idem-0001"), 200, None)
    )
    checks.append(
        (
            await run(
                api, world.a, "What subscriptions do I pay for?", key="conformance-idem-0001"
            ),
            409,
            ErrorEnvelope,
        )
    )
    checks.append(
        (
            await c.post(
                f"/v1/coach/tasks/{'0' * 8}-0000-0000-0000-{'0' * 11}1/complete",
                json={},
                headers=world.a.headers,
            ),
            404,
            ErrorEnvelope,
        )
    )
    checks.append(
        (
            await c.post(
                "/v1/finance/accounts",
                json={"kind": "credit_card", "label": "No limit (demo)"},
                headers=world.a.headers,
            ),
            422,
            ErrorEnvelope,
        )
    )
    saved = api.app.state.memory
    api.app.state.memory = None
    try:
        checks.append(
            (await c.get("/v1/finance/summary", headers=world.a.headers), 503, DetailEnvelope)
        )
    finally:
        api.app.state.memory = saved
    from core.audit.service import ADMIN
    from core.governance import killswitch

    await killswitch.activate(api.sessions, ADMIN, "conformance drill")
    try:
        checks.append((await run(api, world.a, "Show my loan EMI"), 503, ErrorEnvelope))
    finally:
        await killswitch.deactivate(api.sessions, ADMIN, "drill over")
    for resp, status, model in checks:
        assert resp.status_code == status, (resp.request.url, resp.status_code, resp.text)
        if model is not None:
            body = check(model, resp)
            if hasattr(body, "error"):
                assert body.error.code in known, body.error.code
    ok = checks[3][0].json()
    assert ok["status"] == "completed"
    assert checks[2][0].json()["error"]["code"] == "invalid_request"
    assert checks[4][0].json()["error"]["code"] == "idempotency_conflict"
    assert checks[8][0].json()["error"]["code"] == "kill_switch_active"
    # the governance 503 carries no run ids (it stops before a run exists); run-level failures use RunFailedEnvelope
    assert "run_id" not in checks[8][0].json()
    _ = RunFailedEnvelope


async def test_rate_limit_429_uses_the_detail_envelope_with_retry_after(
    migrated_url, sessions
) -> None:
    from tests.integration.conftest import Api as _Api
    from tests.integration.helpers import enroll_device, make_user

    settings: Settings = make_settings(
        migrated_url, rate_limit_user_per_minute=3, rate_limit_ip_per_minute=100000
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.9", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            api = _Api(app, client, settings, sessions)
            dev = await enroll_device(
                sessions, settings, client, await make_user(sessions, "Limited (demo)")
            )
            codes = [
                (await client.get("/v1/finance/upcoming", headers=dev.headers)).status_code
                for _ in range(6)
            ]
            assert 429 in codes
            r = await client.get("/v1/finance/upcoming", headers=dev.headers)
            assert r.status_code == 429 and r.headers["retry-after"].isdigit()
            check(DetailEnvelope, r)
            assert api is not None
    settings = make_settings(
        migrated_url, rate_limit_user_per_minute=100000, rate_limit_ip_per_minute=3
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.10", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            last = None
            for _ in range(6):
                last = await client.get(
                    "/v1/finance/summary"
                )  # unauthenticated: the per-IP limiter answers first
            assert last is not None and last.status_code == 429 and "retry-after" in last.headers
            check(DetailEnvelope, last)


async def test_run_failures_use_the_run_failed_envelope_with_documented_statuses(
    booted: Api,
) -> None:
    """F11: the HTTP status of graph failures, observed end to end (scripted mock adapter, no network)."""
    from core.model_router.adapters import ProviderError
    from tests.integration.test_orchestrator import Scripted, _orch_with

    dev = await device_for(booted, "Failures (demo)")
    original = booted.app.state.orchestrator
    try:
        for replies, code in (
            (["not json", "not json"], "malformed_output"),
            ([ProviderError("boom"), ProviderError("boom")], "provider_error"),
        ):
            booted.app.state.orchestrator = _orch_with(booted, Scripted(replies))
            r = await run(booted, dev, "Plan my day around my goals")
            body = check(RunFailedEnvelope, r)
            assert body.error.code == code, r.text
            assert r.status_code == 502, (code, r.status_code)
            assert body.run_id and body.execution_id
    finally:
        booted.app.state.orchestrator = original
