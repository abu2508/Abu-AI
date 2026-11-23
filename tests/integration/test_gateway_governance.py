"""The gateway enforcing governance with scripted PAID providers (no real provider is ever called)."""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.agent_registry.service import VersionLimits
from core.governance.errors import BudgetExceededError, DuplicateCallError
from core.governance.service import Governance
from core.model_registry import service as registry
from core.model_registry.errors import ApprovalError
from core.model_registry.schemas import Capability, ModelIn, PolicyIn, PricingIn
from core.model_router.adapters import MockAdapter, ProviderError
from core.model_router.gateway import ModelGateway
from core.model_router.router import NoRouteError, ResolvedModel
from core.model_router.types import CompletionRequest, CompletionResult, Message
from core.permissions.errors import LimitExceededError
from core.permissions.runtime import RunContext, start_run
from database.schemas import DataClass, Execution, ExecutionCall, SpendLedger, Tri
from tests.integration.helpers import enable_paid, runnable_agent

D = Decimal
NOW = datetime(2026, 11, 23, tzinfo=UTC)
# Price: USD 20 per million input tokens, USD 100 per million output tokens.
# 100 in + 50 out tokens = (100*20 + 50*100)/1e6 = USD 0.007 = INR 0.70 at a rate of 100.
IN_PRICE, OUT_PRICE = "20", "100"

Behave = Callable[[ResolvedModel, CompletionRequest], Awaitable[CompletionResult]]


def result(model: ResolvedModel, **kw: object) -> CompletionResult:
    base: dict[str, object] = {
        "text": "ok",
        "input_tokens": 100,
        "output_tokens": 50,
        "model_name": model.name,
        "provider": model.provider,
        "latency_ms": 1.0,
    }
    base.update(kw)
    return CompletionResult(**base)


class Scripted:
    def __init__(self, behave: Behave | None = None) -> None:
        self.calls = 0
        self._behave = behave

    async def complete(self, model: ResolvedModel, req: CompletionRequest) -> CompletionResult:
        self.calls += 1
        if self._behave is None:
            return result(model)
        return await self._behave(model, req)


def fail(exc: Exception) -> Behave:
    async def behave(model: ResolvedModel, req: CompletionRequest) -> CompletionResult:
        raise exc

    return behave


def returning(**kw: object) -> Behave:
    async def behave(model: ResolvedModel, req: CompletionRequest) -> CompletionResult:
        return result(model, **kw)

    return behave


def req(max_tokens: int = 4000, dc: DataClass = DataClass.SYNTHETIC) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="hello")], data_class=dc, max_tokens=max_tokens
    )


async def build(
    session: AsyncSession,
    gov: Governance,
    *,
    enabled: bool = True,
    limits: VersionLimits | None = None,
    disable_free: tuple[str, ...] = (),
) -> RunContext:
    """Registers a PAID model and four routes, opts in, and returns a RunContext granted them all."""
    from database.seed.registry import seed_registry

    await seed_registry(session)
    await registry.register_policy(
        session,
        PolicyIn(
            provider="paidco",
            retention_summary="test",
            trains_on_data=Tri.NO,
            human_review=Tri.NO,
            source_url="https://example.test",
            verified_at=NOW,
        ),
    )
    await registry.register_model(
        session,
        ModelIn(
            name="paid-a",
            provider="paidco",
            provider_model_id="paid-a-1",
            gateway_model_name="paid-a",
            capabilities=[Capability.TEXT_GENERATION],
            availability_verified_at=NOW,
            pricing=PricingIn(
                input_per_million=D(IN_PRICE), output_per_million=D(OUT_PRICE), verified_at=NOW
            ),
        ),
    )
    await registry.approve_model(session, "paid-a")
    for alias, name, prio in (
        ("paid-only", "paid-a", 1),
        ("paid-then-free", "paid-a", 1),
        ("paid-then-free", "mock-local", 2),
        ("free-then-paid", "mock-local", 1),
        ("free-then-paid", "paid-a", 2),
        ("disabled-free-then-paid", "litellm-mock", 1),
        ("disabled-free-then-paid", "paid-a", 2),
    ):
        await registry.add_route(session, alias, name, prio)
    for name in disable_free:
        await registry.set_enabled(session, name, False)
    await session.commit()
    if enabled:
        await enable_paid(gov, monthly="300", daily="300", rate="100")
        await registry.set_enabled(session, "paid-a", True)
        await session.commit()
    return await runnable_agent(
        session,
        gov,
        aliases=("paid-only", "paid-then-free", "free-then-paid", "disabled-free-then-paid"),
        limits=limits
        or VersionLimits(
            max_run_cost_inr=D(50), max_run_output_tokens=20_000, timeout_seconds=30, max_steps=20
        ),
    )


def gw(
    session: AsyncSession, gov: Governance, paid: Scripted, free: Scripted | None = None
) -> ModelGateway:
    return ModelGateway(
        session,
        gov,
        {"paidco": paid, "mock": free or MockAdapter(), "litellm-mock": free or MockAdapter()},
    )


async def ledger(sessions: async_sessionmaker[AsyncSession]) -> list[SpendLedger]:
    async with sessions() as s:
        return list(
            (await s.execute(select(SpendLedger).order_by(SpendLedger.created_at))).scalars()
        )


async def calls(sessions: async_sessionmaker[AsyncSession]) -> list[ExecutionCall]:
    async with sessions() as s:
        return list(
            (
                await s.execute(
                    select(ExecutionCall).order_by(ExecutionCall.started_at, ExecutionCall.attempt)
                )
            ).scalars()
        )


# --- paid models are off by default -------------------------------------------------------------------


async def test_paid_models_are_unusable_until_the_operator_opts_in(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov, enabled=False)
    with pytest.raises(ApprovalError, match="not opted in"):
        await registry.set_enabled(session, "paid-a", True)
    await session.execute(
        text("UPDATE models SET enabled = true WHERE name = 'paid-a'")
    )  # simulate a bad manual edit
    await session.commit()
    paid = Scripted()
    with pytest.raises(NoRouteError, match="not permitted by governance"):
        await gw(session, gov, paid).complete(ctx=ctx, alias="paid-only", request=req())
    assert paid.calls == 0 and await ledger(sessions) == []
    (call,) = await calls(sessions)
    assert (call.status, call.failure_reason, call.cost_status) == ("blocked", "no_route", "none")


# --- a normal paid call --------------------------------------------------------------------------------


async def test_paid_call_reserves_settles_and_is_recorded(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    await gw(session, gov, Scripted()).complete(ctx=ctx, alias="paid-only", request=req())
    (row,) = await ledger(sessions)
    assert (row.state, D(str(row.confirmed_inr)), D(row.usd_inr_rate), row.currency) == (
        "settled",
        D("0.7"),
        D(100),
        "INR",
    )
    assert D(row.reserved_inr) > D(str(row.confirmed_inr))  # the worst case was reserved first
    (c,) = await calls(sessions)
    assert (c.status, c.cost_status, c.cost_source, c.currency) == (
        "succeeded",
        "confirmed",
        "registry_price",
        "INR",
    )
    assert (D(c.confirmed_cost_inr or 0), D(c.usd_inr_rate or 0), c.ledger_id) == (
        D("0.7"),
        D(100),
        row.id,
    )
    assert (
        D(c.estimated_cost_inr) == D(row.reserved_inr)
        and c.estimated_cost_inr != c.confirmed_cost_inr
    )  # kept separate
    assert (c.provider, c.provider_model_id, c.model_name, c.model_alias) == (
        "paidco",
        "paid-a-1",
        "paid-a",
        "paid-only",
    )
    assert (c.input_tokens, c.output_tokens, c.fallback_used) == (100, 50, False)
    assert c.policy_decisions["paid"] == "yes" and c.policy_decisions["budget"] == "reserved"
    assert ctx.cost_inr_used == D("0.7")
    assert (await gov.spend.usage()).month_used_inr == D("0.7")


async def test_gateway_reported_cost_is_used_instead_of_registry_price_never_both(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    paid = Scripted(returning(provider_cost_usd=D("0.002")))
    await gw(session, gov, paid).complete(ctx=ctx, alias="paid-only", request=req())
    (row,) = await ledger(sessions)
    assert D(str(row.confirmed_inr)) == D("0.2")  # 0.002 USD * 100 - NOT 0.2 + 0.7
    (c,) = await calls(sessions)
    assert (c.cost_source, D(c.provider_cost_usd or 0)) == ("gateway_reported", D("0.002"))
    assert (await gov.spend.usage()).month_used_inr == D(
        "0.2"
    )  # one ledger row per call: no double counting


# --- unknown cost, timeouts, and provider failures: fail closed ---------------------------------------------


async def test_unknown_cost_is_held_frozen_and_never_zero(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    paid = Scripted(returning(usage_reported=False, input_tokens=0, output_tokens=0))
    res = await gw(session, gov, paid).complete(ctx=ctx, alias="paid-only", request=req())
    assert res.text == "ok"  # the caller still gets its answer ...
    (row,) = await ledger(sessions)
    assert row.state == "held" and row.confirmed_inr is None and D(row.reserved_inr) > 0
    (c,) = await calls(sessions)
    assert (c.cost_status, c.confirmed_cost_inr) == ("unknown", None) and c.policy_decisions[
        "budget"
    ] == "held_unknown_cost"
    usage = await gov.spend.usage()
    assert (
        usage.month_used_inr == usage.held_inr == D(row.reserved_inr) > 0
    )  # ... but spend is NOT recorded as zero
    assert (await gov.spend.snapshot()).paid_frozen
    with pytest.raises(NoRouteError, match="frozen"):  # and paid spending is stopped
        await gw(session, gov, paid).complete(ctx=ctx, alias="paid-only", request=req())
    assert paid.calls == 1


async def test_provider_timeout_holds_the_reservation_and_freezes(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    async def slow(model: ResolvedModel, r: CompletionRequest) -> CompletionResult:
        await asyncio.sleep(10)
        raise AssertionError("unreachable")

    ctx = await build(session, gov, limits=VersionLimits(timeout_seconds=1, max_run_cost_inr=D(50)))
    with pytest.raises(LimitExceededError, match="timeout"):
        await gw(session, gov, Scripted(slow)).complete(ctx=ctx, alias="paid-only", request=req())
    (row,) = await ledger(sessions)
    assert row.state == "held"  # the provider may have processed (and billed) it
    (c,) = await calls(sessions)
    assert (c.status, c.failure_reason, c.cost_status) == ("failed", "timeout", "unknown")
    assert (await gov.spend.snapshot()).paid_frozen and (await gov.spend.usage()).held_inr > 0


async def test_5xx_after_send_may_have_charged_but_a_4xx_rejection_did_not(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(
        session,
        gov,
        limits=VersionLimits(
            max_run_cost_inr=D(50), max_run_output_tokens=20_000, timeout_seconds=30, max_steps=20
        ),
    )
    with pytest.raises(ProviderError):
        await gw(
            session, gov, Scripted(fail(ProviderError("HTTP 500", maybe_charged=True)))
        ).complete(ctx=ctx, alias="paid-only", request=req(max_tokens=2000))
    assert [r.state for r in await ledger(sessions)] == ["held"] and (
        await gov.spend.snapshot()
    ).paid_frozen

    from core.audit.service import ADMIN
    from core.governance import spending

    await spending.unfreeze(gov.factory, ADMIN, "reviewed")
    with pytest.raises(ProviderError):
        await gw(
            session, gov, Scripted(fail(ProviderError("HTTP 401", maybe_charged=False)))
        ).complete(ctx=ctx, alias="paid-only", request=req(max_tokens=2000))
    states = [r.state for r in await ledger(sessions)]
    assert states == ["held", "released"]  # the rejected call cost nothing
    assert not (await gov.spend.snapshot()).paid_frozen
    assert (await gov.spend.usage()).month_used_inr == (
        await gov.spend.usage()
    ).held_inr  # only the held one counts
    assert [c.failure_reason for c in await calls(sessions)] == [
        "provider_error_maybe_charged",
        "provider_rejected",
    ]


async def test_an_unexpected_exception_mid_call_never_leaves_a_dangling_reservation(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    with pytest.raises(RuntimeError):
        await gw(session, gov, Scripted(fail(RuntimeError("adapter bug")))).complete(
            ctx=ctx, alias="paid-only", request=req()
        )
    assert [r.state for r in await ledger(sessions)] == [
        "held"
    ]  # held at worst case, not 'reserved' forever


# --- fallbacks: never silently paid ---------------------------------------------------------------------------


async def test_a_failing_free_model_never_causes_a_paid_call(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    paid, free = Scripted(), Scripted(fail(ProviderError("free model down")))
    with pytest.raises(ProviderError, match="free model down"):
        await gw(session, gov, paid, free).complete(ctx=ctx, alias="free-then-paid", request=req())
    assert (
        free.calls == 1 and paid.calls == 0 and await ledger(sessions) == []
    )  # no paid attempt, no spend


async def test_an_unavailable_free_model_never_routes_to_paid(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov, disable_free=("litellm-mock",))
    paid = Scripted()
    with pytest.raises(NoRouteError, match="never a fallback"):
        await gw(session, gov, paid).complete(
            ctx=ctx, alias="disabled-free-then-paid", request=req()
        )
    assert paid.calls == 0 and await ledger(sessions) == []


async def test_paid_to_free_fallback_is_allowed_and_recorded(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    paid = Scripted(fail(ProviderError("HTTP 503", maybe_charged=True)))
    res = await gw(session, gov, paid).complete(ctx=ctx, alias="paid-then-free", request=req())
    assert res.model_name == "mock-local"
    first, second = await calls(sessions)
    assert (first.attempt, first.status, first.fallback_used) == (0, "failed", False)
    assert (second.attempt, second.status, second.fallback_used, second.fallback_from) == (
        1,
        "succeeded",
        True,
        "paid-a",
    )
    assert second.cost_status == "free" and second.confirmed_cost_inr == 0
    assert [r.state for r in await ledger(sessions)] == [
        "held"
    ]  # the failed paid attempt is accounted for


async def test_exhausted_budget_falls_back_to_the_free_model(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from core.audit.service import ADMIN
    from core.governance import spending

    ctx = await build(session, gov)
    await spending.set_budget(
        gov.factory, ADMIN, D(1), D(1)
    )  # INR 1 cannot cover a worst case of ~INR 40
    paid = Scripted()
    res = await gw(session, gov, paid).complete(ctx=ctx, alias="paid-then-free", request=req())
    assert res.model_name == "mock-local" and paid.calls == 0
    first, second = await calls(sessions)
    assert (first.status, first.failure_reason, first.policy_decisions["budget"]) == (
        "blocked",
        "budget_exceeded_day",
        "refused",
    )
    assert (second.status, second.fallback_used) == ("succeeded", True)


async def test_paid_only_route_with_exhausted_budget_raises_a_controlled_error(
    session: AsyncSession, gov: Governance
) -> None:
    from core.audit.service import ADMIN
    from core.governance import spending

    ctx = await build(session, gov)
    await spending.set_budget(gov.factory, ADMIN, D(1), D(1))
    with pytest.raises(BudgetExceededError):
        await gw(session, gov, Scripted()).complete(ctx=ctx, alias="paid-only", request=req())


# --- per-run budget ----------------------------------------------------------------------------------------------


async def test_a_single_call_cannot_exceed_the_per_run_budget(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(
        session, gov, limits=VersionLimits(max_run_cost_inr=D(30), max_run_output_tokens=20_000)
    )
    paid = Scripted()
    with pytest.raises(BudgetExceededError) as exc:  # worst case ~INR 40 > INR 30 for the whole run
        await gw(session, gov, paid).complete(
            ctx=ctx, alias="paid-only", request=req(max_tokens=4000)
        )
    assert exc.value.scope == "run" and paid.calls == 0 and await ledger(sessions) == []
    await gw(session, gov, paid).complete(
        ctx=ctx, alias="paid-only", request=req(max_tokens=2000)
    )  # ~INR 20 fits
    assert paid.calls == 1


async def test_run_budget_counts_earlier_unresolved_spend_in_the_same_run(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await build(
        session, gov, limits=VersionLimits(max_run_cost_inr=D(50), max_run_output_tokens=20_000)
    )
    await gov.spend.reserve(
        execution_id=ctx.execution_id,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        provider="paidco",
        model_name="paid-a",
        call_key="earlier",
        worst_case_usd=D("0.30"),
        run_limit_inr=D(50),
    )  # INR 30 already held by this run
    with pytest.raises(BudgetExceededError, match="run"):
        await gw(session, gov, Scripted()).complete(
            ctx=ctx, alias="paid-only", request=req(max_tokens=2500)
        )  # +~INR 25


# --- concurrency and retries --------------------------------------------------------------------------------------


async def test_concurrent_gateway_calls_cannot_overspend(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from core.audit.service import ADMIN
    from core.governance import spending

    first = await build(session, gov)
    await spending.set_budget(gov.factory, ADMIN, D(100), D(100))

    async def slow(model: ResolvedModel, r: CompletionRequest) -> CompletionResult:
        await asyncio.sleep(0.6)  # keep the reservations in flight so the calls genuinely overlap
        return result(model)

    paid = Scripted(slow)
    contexts = [first]
    for _ in range(9):
        async with sessions() as s:
            contexts.append(
                await start_run(s, user_id=first.user_id, agent_id="finance_agent", governance=gov)
            )

    async def one(ctx: RunContext) -> object:
        async with sessions() as s:
            try:
                return await gw(s, gov, paid).complete(
                    ctx=ctx, alias="paid-only", request=req(max_tokens=4000)
                )  # ~INR 40
            except BudgetExceededError as exc:
                return exc

    outcomes = await asyncio.gather(*(one(c) for c in contexts))
    ok = [o for o in outcomes if isinstance(o, CompletionResult)]
    refused = [o for o in outcomes if isinstance(o, BudgetExceededError)]
    assert len(ok) + len(refused) == 10
    assert len(ok) == 2 and len(refused) == 8  # 2 x ~40 = ~80 fits in 100; a third (~120) cannot
    assert paid.calls == 2  # refused calls never reached the provider
    rows = await ledger(sessions)
    assert len(rows) == 2 and all(
        r.state == "settled" for r in rows
    )  # nothing left dangling in 'reserved'
    assert sum(D(r.confirmed_inr or 0) for r in rows) <= 100


async def test_replaying_the_same_call_key_never_calls_the_provider_or_charges_twice(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    step = ctx.steps_used + 1  # the key the gateway is about to use for this call
    await gov.spend.reserve(
        execution_id=ctx.execution_id,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        provider="paidco",
        model_name="paid-a",
        call_key=f"{ctx.execution_id}:{step}:0",
        worst_case_usd=D("0.1"),
        run_limit_inr=D(50),
    )  # i.e. this attempt was already made before a crash/retry
    paid = Scripted()
    with pytest.raises(DuplicateCallError):
        await gw(session, gov, paid).complete(ctx=ctx, alias="paid-only", request=req())
    assert paid.calls == 0 and len(await ledger(sessions)) == 1
    (c,) = await calls(sessions)
    assert c.failure_reason == "duplicate_call"


async def test_a_genuine_retry_is_a_new_attempt_with_its_own_key(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    from core.audit.service import ADMIN
    from core.governance import spending

    ctx = await build(
        session,
        gov,
        limits=VersionLimits(
            max_run_cost_inr=D(50), max_run_output_tokens=20_000, timeout_seconds=30, max_steps=20
        ),
    )
    paid = Scripted(fail(ProviderError("HTTP 500", maybe_charged=True)))
    with pytest.raises(ProviderError):
        await gw(session, gov, paid).complete(
            ctx=ctx, alias="paid-only", request=req(max_tokens=2000)
        )
    await spending.unfreeze(gov.factory, ADMIN, "retrying")
    await gw(session, gov, Scripted()).complete(
        ctx=ctx, alias="paid-only", request=req(max_tokens=2000)
    )
    rows = await ledger(sessions)
    assert [r.state for r in rows] == ["held", "settled"] and len({r.call_key for r in rows}) == 2


async def test_execution_totals_aggregate_only_successful_calls(
    session: AsyncSession, gov: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    ctx = await build(session, gov)
    await gw(session, gov, Scripted(), None).complete(ctx=ctx, alias="paid-only", request=req())
    await gw(session, gov, Scripted(), None).complete(ctx=ctx, alias="paid-only", request=req())
    await gov.finish(ctx.execution_id, "succeeded")
    async with sessions() as s:
        ex = await s.get(Execution, ctx.execution_id)
    assert ex is not None
    assert (ex.status, ex.call_count, ex.total_input_tokens, ex.total_output_tokens) == (
        "succeeded",
        2,
        200,
        100,
    )
    assert D(ex.total_confirmed_cost_inr) == D("1.4") and ex.completed_at is not None
