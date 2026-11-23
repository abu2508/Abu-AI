"""The kill switch: who may flip it, what it blocks, what it must leave alone, and fail-closed."""

import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from core.agent_registry import service as agents
from core.audit.service import ADMIN, Actor
from core.governance import killswitch
from core.governance.errors import GovernanceUnavailableError, KillSwitchActiveError
from core.governance.service import Governance
from core.model_router.adapters import MockAdapter
from core.model_router.gateway import ModelGateway
from core.model_router.types import CompletionRequest, Message
from core.permissions.errors import PermissionDeniedError
from core.permissions.runtime import RunContext, start_run
from core.permissions.tools import ToolExecutor, ToolRegistry, ToolRisk, ToolSpec
from database.schemas import AuditEvent, DataClass, Execution, KillSwitchState
from database.seed.registry import MOCK_ALIAS
from services.api.main import create_app
from tests.integration.conftest import Api
from tests.integration.helpers import (
    Device,
    enroll_device,
    make_settings,
    runnable_agent,
)


def request() -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="hello")], data_class=DataClass.SYNTHETIC
    )


async def events(sessions: async_sessionmaker[AsyncSession], kind: str) -> list[AuditEvent]:
    async with sessions() as s:
        return list(
            (
                await s.execute(
                    select(AuditEvent).where(AuditEvent.event_type == kind).order_by(AuditEvent.id)
                )
            ).scalars()
        )


async def state(sessions: async_sessionmaker[AsyncSession]) -> KillSwitchState:
    async with sessions() as s:
        return (await s.execute(select(KillSwitchState))).scalar_one()


# --- activation: who, when, why, through which interface ------------------------------------------------


async def test_app_can_activate_and_the_activation_is_fully_recorded(
    api: Api, device: Device
) -> None:
    before = datetime.now(UTC)
    resp = await api.client.post(
        "/v1/governance/kill-switch",
        json={"reason": "phone lost, stopping everything"},
        headers=device.headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert (body["active"], body["activated_by"], body["activated_via"]) == (True, "app", "api")
    row = await state(api.sessions)
    assert (row.active, row.reason, row.activated_by_type, row.activated_via) == (
        True,
        "phone lost, stopping everything",
        "user",
        "api",
    )
    assert (
        row.activated_by_user_id == device.user_id
        and row.activated_at is not None
        and row.activated_at >= before
    )
    (ev,) = await events(api.sessions, "kill_switch.activated")
    assert (ev.actor_type, ev.actor_user_id, ev.actor_session_id) == (
        "user",
        device.user_id,
        device.session_id,
    )
    assert ev.detail == {"reason": "phone lost, stopping everything", "via": "api"}


async def test_operator_cli_can_activate_and_it_says_so(api: Api) -> None:
    snap = await killswitch.activate(api.sessions, ADMIN, "maintenance")
    assert (snap.active, snap.activated_by_type, snap.activated_via, snap.reason) == (
        True,
        "admin",
        "cli",
        "maintenance",
    )
    (ev,) = await events(api.sessions, "kill_switch.activated")
    assert (ev.actor_type, ev.detail["via"]) == ("admin", "cli")


async def test_a_second_activation_keeps_the_original_record_and_is_still_audited(
    api: Api, device: Device
) -> None:
    await killswitch.activate(api.sessions, ADMIN, "first reason")
    resp = await api.client.post(
        "/v1/governance/kill-switch", json={"reason": "second"}, headers=device.headers
    )
    assert (
        resp.status_code == 200
        and resp.json()["activated_by"] == "operator"
        and resp.json()["reason"] == "first reason"
    )
    assert (await state(api.sessions)).activated_via == "cli"
    (ignored,) = await events(api.sessions, "kill_switch.activate_ignored")
    assert ignored.actor_type == "user"


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"reason": ""},
        {"reason": "   "},
        {"reason": "x" * 501},
        {"reason": "ok", "active": False},
    ],
)
async def test_activation_needs_a_valid_reason(
    api: Api, device: Device, body: dict[str, object]
) -> None:
    resp = await api.client.post("/v1/governance/kill-switch", json=body, headers=device.headers)
    assert resp.status_code in (422, 400)
    assert (await state(api.sessions)).active is False


async def test_activation_requires_authentication(api: Api) -> None:
    assert (
        await api.client.post("/v1/governance/kill-switch", json={"reason": "x"})
    ).status_code == 401
    assert (await state(api.sessions)).active is False


# --- only the operator CLI may deactivate -----------------------------------------------------------------------


async def test_operator_can_deactivate_and_execution_resumes(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    await killswitch.activate(gov.factory, ADMIN, "stop")
    with pytest.raises(KillSwitchActiveError):
        await gov.require_execution_allowed()
    snap = await killswitch.deactivate(gov.factory, ADMIN, "all clear")
    assert snap.active is False and snap.reason is None
    await gov.require_execution_allowed()
    assert (
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)
    ).agent_id == "finance_agent"
    (ev,) = await events(gov.factory, "kill_switch.deactivated")
    assert (
        ev.actor_type == "admin"
        and ev.detail["reason"] == "all clear"
        and ev.detail["was_reason"] == "stop"
    )


async def test_a_user_actor_cannot_deactivate_and_the_attempt_is_audited(gov: Governance) -> None:
    await killswitch.activate(gov.factory, ADMIN, "stop")
    user = Actor.user(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(PermissionDeniedError, match="operator CLI"):
        await killswitch.deactivate(gov.factory, user, "let me back in")
    assert (await state(gov.factory)).active is True
    (ev,) = await events(gov.factory, "governance.privileged_action_denied")
    assert ev.actor_user_id == user.user_id and ev.detail["action"] == "kill_switch.deactivate"


async def test_app_token_cannot_deactivate_the_kill_switch(api: Api, device: Device) -> None:
    await killswitch.activate(api.sessions, ADMIN, "stop")
    for method, path in (
        ("DELETE", "/v1/governance/kill-switch"),
        ("POST", "/v1/governance/kill-switch/deactivate"),
        ("PATCH", "/v1/governance/kill-switch"),
    ):
        resp = await api.client.request(
            method, path, headers=device.headers, json={"reason": "x", "active": False}
        )
        assert resp.status_code in (403, 404, 405)
    assert (await state(api.sessions)).active is True
    denied = await events(api.sessions, "governance.privileged_action_denied")
    assert [(e.actor_type, e.actor_user_id, e.detail["action"]) for e in denied] == [
        ("user", device.user_id, "kill_switch.deactivate")
    ]


async def test_app_token_cannot_modify_budgets_models_or_providers(
    api: Api, device: Device
) -> None:
    for path, _action in (
        ("/v1/governance/spending", "spending.modify"),
        ("/v1/governance/models", "model_policy.modify"),
        ("/v1/governance/providers", "provider_config.modify"),
    ):
        resp = await api.client.put(
            path,
            headers=device.headers,
            json={"monthly_budget_inr": 300, "paid_models_enabled": True},
        )
        assert resp.status_code == 403, path
    async with api.sessions() as s:
        cfg = (
            await s.execute(
                text(
                    "SELECT monthly_budget_inr, paid_models_enabled, usd_inr_rate FROM spending_config"
                )
            )
        ).one()
    assert (cfg[0], cfg[1], cfg[2]) == (0, False, None)  # untouched: default spend is INR 0
    actions = [
        e.detail["action"]
        for e in await events(api.sessions, "governance.privileged_action_denied")
    ]
    assert actions == ["spending.modify", "model_policy.modify", "provider_config.modify"]


async def test_app_token_cannot_reenable_an_agent_but_can_disable_one(
    api: Api, device: Device, session: AsyncSession, gov: Governance
) -> None:
    await runnable_agent(session, gov, user_id=device.user_id)
    kill = await api.client.post(
        "/v1/governance/kill-switch", json={"reason": "stop"}, headers=device.headers
    )
    assert kill.status_code == 200
    disable = await api.client.post(
        "/v1/agents/finance_agent/disable", headers=device.headers
    )  # allowed even while killed
    assert disable.status_code == 200 and disable.json()["enabled"] is False
    enable = await api.client.post("/v1/agents/finance_agent/enable", headers=device.headers)
    assert enable.status_code == 403
    assert (await api.client.get("/v1/agents/finance_agent", headers=device.headers)).json()[
        "enabled"
    ] is False
    assert [
        e.detail["action"]
        for e in await events(api.sessions, "governance.privileged_action_denied")
    ] == ["agent.enable:finance_agent"]
    async with api.sessions() as s, s.begin():  # and the service layer refuses a user actor too
        with pytest.raises(PermissionDeniedError, match="operator-only"):
            await agents.set_agent_enabled(s, "finance_agent", True, Actor.user(device.user_id))
        await agents.set_agent_enabled(s, "finance_agent", True, ADMIN)  # the operator can


# --- what it blocks: every execution path -----------------------------------------------------------------------------


async def test_kill_switch_blocks_every_way_of_starting_work(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)  # a run that was already in progress
    gateway = ModelGateway(session, gov, {"mock": MockAdapter()})
    reg = ToolRegistry()

    async def handler(c: RunContext, a: object) -> str:
        return "ran"

    from pydantic import BaseModel, ConfigDict

    class Args(BaseModel):
        model_config = ConfigDict(extra="forbid")

    reg.register(ToolSpec("lookup", "r", Args, ToolRisk.READ, handler))
    ctx.tools["lookup"] = False  # as if granted
    await gateway.complete(ctx=ctx, alias=MOCK_ALIAS, request=request())  # works before ...
    async with gov.factory() as s:
        before = len((await s.execute(select(Execution))).scalars().all())

    await killswitch.activate(gov.factory, ADMIN, "stop")

    with pytest.raises(KillSwitchActiveError):  # 1. a new agent execution
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)
    with pytest.raises(
        KillSwitchActiveError
    ):  # 2. a DIRECT gateway call, even with a pre-existing context
        await gateway.complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    with pytest.raises(KillSwitchActiveError):  # 3. a tool execution
        await ToolExecutor(session, gov.factory, gov, reg).execute(ctx, "lookup", {})

    async def dequeue_and_run(
        job: dict[str, str],
    ) -> RunContext:  # 4. queued/scheduled work starts via start_run
        return await start_run(
            session,
            user_id=ctx.user_id,
            agent_id=job["agent"],
            governance=gov,
            task_type="scheduled",
        )

    with pytest.raises(KillSwitchActiveError):
        await dequeue_and_run({"agent": "finance_agent"})

    async with gov.factory() as s:
        assert (
            len((await s.execute(select(Execution))).scalars().all()) == before
        )  # nothing new started
    blocked = await events(gov.factory, "execution.blocked")
    assert len(blocked) == 2 and all(e.detail["reason"] == "KillSwitchActiveError" for e in blocked)


async def test_in_progress_context_cannot_be_used_to_call_the_model_after_activation(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    gateway = ModelGateway(session, gov, {"mock": MockAdapter()})
    await killswitch.activate(gov.factory, ADMIN, "stop")
    for _ in range(3):
        with pytest.raises(KillSwitchActiveError):
            await gateway.complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    assert ctx.steps_used == 0  # it did not even consume a step


# --- what it must NOT block --------------------------------------------------------------------------------------------------


async def test_reads_audit_sessions_and_revocation_stay_available(
    api: Api, device: Device, session: AsyncSession, gov: Governance
) -> None:
    await runnable_agent(session, gov, user_id=device.user_id)
    other = await enroll_device(api.sessions, api.settings, api.client, device.user_id, "iPad")
    await killswitch.activate(api.sessions, ADMIN, "stop")
    h = device.headers
    for path in (
        "/v1/me",
        "/v1/agents",
        "/v1/agents/finance_agent",
        "/v1/audit",
        "/v1/governance/status",
        "/v1/executions",
        "/v1/auth/sessions",
        "/health/ready",
    ):
        resp = await api.client.get(path, headers=h)
        assert resp.status_code in (200, 503), (
            path
        )  # /health/ready may be 503 only because LiteLLM isn't running here
        assert resp.status_code == 200 or path == "/health/ready", path
    status = (await api.client.get("/v1/governance/status", headers=h)).json()
    assert (
        status["kill_switch"]["active"] is True and status["kill_switch"]["activated_via"] == "cli"
    )
    refreshed = await api.client.post(
        "/v1/auth/refresh", json={"refresh_token": device.refresh}
    )  # auth keeps working
    assert refreshed.status_code == 200
    assert (
        await api.client.delete(f"/v1/auth/sessions/{other.session_id}", headers=h)
    ).status_code == 204  # revocation works
    async with api.sessions() as s:  # personal data stays readable
        assert (await s.execute(text("SELECT count(*) FROM users"))).scalar_one() == 1


# --- persistence and fail-closed ---------------------------------------------------------------------------------------------------


async def test_kill_switch_survives_a_restart(
    api: Api, device: Device, migrated_url: object
) -> None:
    await api.client.post(
        "/v1/governance/kill-switch", json={"reason": "persist me"}, headers=device.headers
    )
    # "Restart": a brand-new engine/sessionmaker/Governance with no in-process state at all ...
    engine = create_async_engine(migrated_url)  # type: ignore[arg-type]
    try:
        fresh = Governance(async_sessionmaker(engine, expire_on_commit=False))
        with pytest.raises(KillSwitchActiveError):
            await fresh.require_execution_allowed()
        assert (await killswitch.read_state(fresh.factory)).reason == "persist me"
    finally:
        await engine.dispose()
    # ... and a brand-new application instance.
    app = create_app(make_settings(migrated_url))  # type: ignore[arg-type]
    async with app.router.lifespan_context(app):
        import httpx

        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://t"
        ) as c:
            r = await c.get("/v1/governance/status", headers=device.headers)
    assert r.json()["kill_switch"]["active"] is True


async def test_missing_state_row_fails_closed_everywhere(
    api: Api, device: Device, session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov, user_id=device.user_id)
    gateway = ModelGateway(session, gov, {"mock": MockAdapter()})
    await session.execute(
        text("ALTER TABLE kill_switch_state DISABLE TRIGGER kill_switch_state_no_delete")
    )
    await session.execute(text("DELETE FROM kill_switch_state"))
    await session.commit()
    with pytest.raises(GovernanceUnavailableError):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)
    with pytest.raises(GovernanceUnavailableError):
        await gateway.complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    with pytest.raises(GovernanceUnavailableError):
        await killswitch.activate(
            gov.factory, ADMIN, "x"
        )  # cannot even record state: refuse, don't guess
    status = await api.client.get("/v1/governance/status", headers=device.headers)
    assert status.status_code == 503 and "blocked" in status.text  # honest, not a guess
    assert (
        await api.client.get("/v1/me", headers=device.headers)
    ).status_code == 200  # auth is unaffected


async def test_unreachable_database_fails_closed() -> None:
    engine = create_async_engine(
        "postgresql+psycopg://nobody:x@127.0.0.1:1/none", connect_args={"connect_timeout": 1}
    )
    try:
        gov = Governance(async_sessionmaker(engine))
        with pytest.raises(GovernanceUnavailableError):
            await gov.require_execution_allowed()
        with pytest.raises(GovernanceUnavailableError):
            await gov.policy()
    finally:
        await engine.dispose()


# --- audit survives rollback ---------------------------------------------------------------------------------------------------------


async def test_denial_and_block_audit_records_survive_a_rolled_back_transaction(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    user = Actor.user(ctx.user_id)
    await killswitch.deny_privileged_action(gov.factory, user, "spending.modify")
    await killswitch.activate(gov.factory, ADMIN, "stop")
    with pytest.raises(KillSwitchActiveError):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)
    await session.rollback()  # the request's own transaction is thrown away ...
    assert (
        len(await events(gov.factory, "governance.privileged_action_denied")) == 1
    )  # ... the records are still there
    assert len(await events(gov.factory, "kill_switch.activated")) == 1
    assert len(await events(gov.factory, "execution.blocked")) == 1


async def test_execution_records_survive_a_rolled_back_request(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    await session.rollback()
    async with gov.factory() as s:
        assert await s.get(Execution, ctx.execution_id) is not None


async def test_unauthenticated_user_cannot_read_governance_status(api: Api) -> None:
    assert (await api.client.get("/v1/governance/status")).status_code == 401
    assert (await api.client.get("/v1/executions")).status_code == 401


async def test_secret_values_do_not_appear_in_kill_switch_audit(api: Api, device: Device) -> None:
    await api.client.post(
        "/v1/governance/kill-switch", json={"reason": "stolen"}, headers=device.headers
    )
    body = (await api.client.get("/v1/audit?limit=200", headers=device.headers)).text
    for secret in (device.access, device.refresh):
        assert secret not in body
