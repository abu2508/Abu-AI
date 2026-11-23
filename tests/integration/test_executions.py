"""Execution records: complete, durable, and free of prompts and tool arguments."""

import json
import uuid
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.governance import executions
from core.governance.service import Governance
from core.model_router.adapters import MockAdapter
from core.model_router.gateway import ModelGateway
from core.model_router.types import CompletionRequest, Message
from core.permissions.runtime import start_run
from database.schemas import Base, DataClass, Execution, ExecutionCall
from database.seed.registry import MOCK_ALIAS
from tests.integration.conftest import Api
from tests.integration.helpers import Device, enroll_device, make_user, runnable_agent

SENSITIVE_PROMPT = "MY-SALARY-IS-987654-AND-ACCOUNT-1234567890"


def req(text_: str = "hello", dc: DataClass = DataClass.SENSITIVE) -> CompletionRequest:
    return CompletionRequest(messages=[Message(role="user", content=text_)], data_class=dc)


async def test_start_run_records_the_execution_with_identity_and_trace(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov, task_type="categorize_transaction")
    assert len(ctx.trace_id) == 32  # generated
    async with gov.factory() as s:
        ex = await s.get(Execution, ctx.execution_id)
    assert ex is not None
    assert (ex.user_id, ex.agent_id, ex.agent_version_id, ex.agent_version) == (
        ctx.user_id,
        "finance_agent",
        ctx.version_id,
        1,
    )
    assert (ex.task_type, ex.status, ex.trace_id, ex.failure_reason) == (
        "categorize_transaction",
        "running",
        ctx.trace_id,
        None,
    )
    assert ex.started_at is not None and ex.completed_at is None


async def test_a_caller_supplied_trace_id_is_propagated_and_validated(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    traced = await start_run(
        session,
        user_id=ctx.user_id,
        agent_id="finance_agent",
        governance=gov,
        trace_id="req-abc12345",
    )
    assert traced.trace_id == "req-abc12345"
    for bad in ("short", "has spaces in it!", "x" * 80, "a/b/c/d/e/f"):
        with pytest.raises(ValueError, match="trace"):
            await start_run(
                session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov, trace_id=bad
            )
    with pytest.raises(ValueError, match="task_type"):
        await start_run(
            session,
            user_id=ctx.user_id,
            agent_id="finance_agent",
            governance=gov,
            task_type="Not A Slug",
        )


async def test_every_model_call_is_recorded_with_the_required_fields(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    await ModelGateway(session, gov, {"mock": MockAdapter()}).complete(
        ctx=ctx, alias=MOCK_ALIAS, request=req()
    )
    async with gov.factory() as s:
        (c,) = (await s.execute(select(ExecutionCall))).scalars().all()
    assert (c.execution_id, c.step, c.attempt, c.status, c.failure_reason) == (
        ctx.execution_id,
        1,
        0,
        "succeeded",
        None,
    )
    assert (c.model_alias, c.model_name, c.provider, c.provider_model_id) == (
        MOCK_ALIAS,
        "mock-local",
        "mock",
        "mock-local",
    )
    assert (
        c.input_tokens > 0
        and c.output_tokens > 0
        and c.latency_ms is not None
        and c.latency_ms >= 0
    )
    assert (c.cost_status, c.estimated_cost_inr, c.confirmed_cost_inr, c.currency) == (
        "free",
        Decimal(0),
        Decimal(0),
        "INR",
    )
    assert (c.fallback_used, c.fallback_from, c.ledger_id, c.usd_inr_rate) == (
        False,
        None,
        None,
        None,
    )
    assert c.policy_decisions == {
        "kill_switch": "inactive",
        "data_class": "sensitive",
        "paid": "no",
    }
    assert (
        c.started_at is not None and c.completed_at is not None and c.completed_at >= c.started_at
    )


async def test_finishing_aggregates_and_the_first_finish_wins(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    gw = ModelGateway(session, gov, {"mock": MockAdapter()})
    await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=req())
    await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=req())
    await gov.finish(ctx.execution_id, "succeeded")
    await gov.finish(
        ctx.execution_id, "failed", "late_error"
    )  # a later report cannot rewrite history
    async with gov.factory() as s:
        ex = await s.get(Execution, ctx.execution_id)
    assert ex is not None
    assert (ex.status, ex.failure_reason, ex.call_count) == ("succeeded", None, 2)
    assert ex.total_input_tokens > 0 and ex.completed_at is not None
    with pytest.raises(ValueError, match="invalid final status"):
        await gov.finish(ctx.execution_id, "weird")


async def test_failure_reasons_are_machine_codes_even_if_a_caller_passes_prose(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    await gov.finish(ctx.execution_id, "failed", f"the user said {SENSITIVE_PROMPT}")
    async with gov.factory() as s:
        ex = await s.get(Execution, ctx.execution_id)
    assert ex is not None and ex.failure_reason == "unspecified"
    cid = await gov.call(
        execution_id=ctx.execution_id,
        step=9,
        attempt=0,
        status="failed",
        failure_reason=f"bad: {SENSITIVE_PROMPT}",
        model_alias="x",
        cost_status="none",
    )
    async with gov.factory() as s:
        call = await s.get(ExecutionCall, cid)
    assert call is not None and call.failure_reason == "unspecified"


# --- confidentiality ----------------------------------------------------------------------------------------------


async def test_prompts_never_appear_in_any_governance_or_audit_table(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    gw = ModelGateway(session, gov, {"mock": MockAdapter()})
    await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=req(f"my details: {SENSITIVE_PROMPT}"))
    await gov.finish(ctx.execution_id, "succeeded")
    dump: list[str] = []
    async with gov.factory() as s:
        for table in (
            "executions",
            "execution_calls",
            "spend_ledger",
            "audit_events",
            "kill_switch_state",
            "spending_config",
        ):
            rows = (await s.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))).scalars()
            dump += list(rows)
    blob = "\n".join(dump)
    assert (
        "executions"
        and SENSITIVE_PROMPT not in blob
        and "987654" not in blob
        and "1234567890" not in blob
    )


def test_no_governance_column_is_able_to_hold_content() -> None:
    forbidden = {
        "prompt",
        "content",
        "messages",
        "completion",
        "text",
        "args",
        "arguments",
        "body",
        "payload",
        "response",
    }
    for table in (
        "executions",
        "execution_calls",
        "spend_ledger",
        "kill_switch_state",
        "spending_config",
    ):
        columns = {c.name for c in Base.metadata.tables[table].columns}
        assert not (columns & forbidden), (table, columns & forbidden)


# --- API: own records only, redacted ------------------------------------------------------------------------------------


async def test_api_lists_only_the_callers_executions(
    api: Api, device: Device, session: AsyncSession, gov: Governance
) -> None:
    mine = await runnable_agent(session, gov, user_id=device.user_id)
    await ModelGateway(session, gov, {"mock": MockAdapter()}).complete(
        ctx=mine, alias=MOCK_ALIAS, request=req(SENSITIVE_PROMPT)
    )
    await gov.finish(mine.execution_id, "succeeded")

    other_user = await make_user(api.sessions, "Other")
    other_ctx = await start_run(
        session, user_id=other_user, agent_id="finance_agent", governance=gov, task_type="theirs"
    )
    other_dev = await enroll_device(
        api.sessions, api.settings, api.client, other_user, "OtherPhone"
    )

    listing = (await api.client.get("/v1/executions", headers=device.headers)).json()
    assert [e["id"] for e in listing] == [str(mine.execution_id)]
    assert listing[0]["status"] == "succeeded" and listing[0]["call_count"] == 1
    detail = (
        await api.client.get(f"/v1/executions/{mine.execution_id}", headers=device.headers)
    ).json()
    assert len(detail["calls"]) == 1 and detail["calls"][0]["cost_status"] == "free"
    assert SENSITIVE_PROMPT not in json.dumps(detail) and "user_id" not in detail

    assert (
        await api.client.get(f"/v1/executions/{other_ctx.execution_id}", headers=device.headers)
    ).status_code == 404
    assert (
        await api.client.get(f"/v1/executions/{mine.execution_id}", headers=other_dev.headers)
    ).status_code == 404
    assert (
        await api.client.get(f"/v1/executions/{uuid.uuid4()}", headers=device.headers)
    ).status_code == 404
    assert [
        e["task_type"]
        for e in (await api.client.get("/v1/executions", headers=other_dev.headers)).json()
    ] == ["theirs"]


def test_safe_reason_helper_is_the_only_writer_of_reasons() -> None:
    assert executions.safe_reason(None) is None and executions.safe_reason("ok_code") == "ok_code"


async def test_executions_are_written_with_utc_timestamps(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    async with gov.factory() as s:
        ex = await s.get(Execution, ctx.execution_id)
    assert ex is not None and ex.started_at.tzinfo is not None
    assert abs((datetime.now(UTC) - ex.started_at).total_seconds()) < 60


async def test_sessionmaker_fixture_is_isolated_per_test(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    async with sessions() as s:
        assert (await s.execute(select(Execution))).first() is None
