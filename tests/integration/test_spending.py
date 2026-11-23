"""The spending controller against a real database: concurrency, budgets, rollover, fail-closed."""

import asyncio
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import ADMIN, Actor
from core.governance import killswitch, spending
from core.governance.errors import (
    BudgetExceededError,
    GovernanceError,
    GovernanceUnavailableError,
    KillSwitchActiveError,
    PaidSpendingBlockedError,
)
from core.governance.service import Governance
from core.permissions.errors import PermissionDeniedError
from core.permissions.runtime import RunContext
from database.schemas import AuditEvent, SpendLedger
from tests.integration.helpers import enable_paid, runnable_agent

D = Decimal
RATE = D("100")  # INR per USD: makes the arithmetic obvious (USD 0.40 = INR 40)


class Clock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


NOON_IST_23_NOV = datetime(2026, 11, 23, 6, 30, tzinfo=UTC)  # 12:00 IST


@pytest.fixture
def clock() -> Clock:
    return Clock(NOON_IST_23_NOV)


@pytest.fixture
async def g(sessions: async_sessionmaker[AsyncSession], clock: Clock) -> Governance:
    return Governance(sessions, clock)


@pytest.fixture
async def ctx(session: AsyncSession, g: Governance) -> RunContext:
    return await runnable_agent(session, g)


async def reserve(
    g: Governance, ctx: RunContext, key: str | None = None, inr: str = "10", run_limit: str = "1000"
) -> spending.Reservation:
    return await g.spend.reserve(
        execution_id=ctx.execution_id,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        provider="paidco",
        model_name="paid-1",
        call_key=key or uuid.uuid4().hex,
        worst_case_usd=D(inr) / RATE,
        run_limit_inr=D(run_limit),
    )


async def counted(sessions: async_sessionmaker[AsyncSession]) -> Decimal:
    async with sessions() as s:
        rows = (await s.execute(select(SpendLedger))).scalars().all()
    return sum(
        (
            D(str(r.confirmed_inr))
            if r.state == "settled"
            else D(0)
            if r.state == "released"
            else D(r.reserved_inr)
            for r in rows
        ),
        D(0),
    )


# --- defaults and the absolute ceiling ------------------------------------------------------------


async def test_default_is_zero_spend_and_paid_is_blocked(g: Governance, ctx: RunContext) -> None:
    snap = await g.spend.snapshot()
    assert (snap.monthly_budget_inr, snap.daily_budget_inr, snap.paid_models_enabled) == (
        D(0),
        D(0),
        False,
    )
    assert snap.usd_inr_rate is None
    with pytest.raises(PaidSpendingBlockedError, match="disabled"):
        await reserve(g, ctx)
    assert (await g.spend.usage()).month_used_inr == 0


async def test_budget_cannot_exceed_300_inr_even_for_the_operator(
    g: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await spending.set_budget(g.factory, ADMIN, D("300"), D("300"))  # exactly the ceiling is fine
    for monthly, daily in (
        ("300.01", "10"),
        ("1000", "10"),
        ("-1", "0"),
        ("100", "101"),
        ("100", "-5"),
    ):
        with pytest.raises(ValueError, match="budget"):
            await spending.set_budget(g.factory, ADMIN, D(monthly), D(daily))
    snap = await g.spend.snapshot()
    assert snap.monthly_budget_inr == 300  # rejected attempts changed nothing
    async with sessions() as s:
        rejected = (
            (
                await s.execute(
                    select(AuditEvent).where(AuditEvent.event_type == "spending.config_rejected")
                )
            )
            .scalars()
            .all()
        )
    assert len(rejected) == 5 and all(
        r.actor_type == "admin" for r in rejected
    )  # rejections are audited


async def test_database_enforces_the_ceiling_even_if_the_service_is_bypassed(
    session: AsyncSession,
) -> None:
    for sql in (
        "UPDATE spending_config SET monthly_budget_inr = 300.01",
        "UPDATE spending_config SET monthly_budget_inr = 100, daily_budget_inr = 200",
        "UPDATE spending_config SET usd_inr_rate = 0",
    ):
        async with session.begin_nested() as sp:
            with pytest.raises((IntegrityError, DBAPIError)):
                await session.execute(text(sql))
            await sp.rollback()


async def test_enabling_paid_requires_budget_and_explicit_rate(g: Governance) -> None:
    with pytest.raises(ValueError, match="before enabling"):
        await spending.set_paid_models_enabled(g.factory, ADMIN, True)
    await spending.set_budget(g.factory, ADMIN, D(100), D(50))
    with pytest.raises(ValueError, match="before enabling"):  # still no rate
        await spending.set_paid_models_enabled(g.factory, ADMIN, True)
    with pytest.raises(ValueError, match="source"):
        await spending.set_rate(g.factory, ADMIN, D(90), "  ")
    with pytest.raises(ValueError):
        await spending.set_rate(g.factory, ADMIN, D(0), "x")
    await spending.set_rate(g.factory, ADMIN, D("88.5"), "RBI reference, 2026-11-23")
    await spending.set_paid_models_enabled(g.factory, ADMIN, True)
    snap = await g.spend.snapshot()
    assert (snap.usd_inr_rate, snap.rate_source) == (D("88.5"), "RBI reference, 2026-11-23")


async def test_stale_rate_fails_closed(g: Governance, ctx: RunContext, clock: Clock) -> None:
    await enable_paid(g, rate="100")
    await reserve(g, ctx)
    clock.now += timedelta(days=15)
    with pytest.raises(PaidSpendingBlockedError, match="stale"):
        await reserve(g, ctx)


# --- reservations, settlement and rounding ---------------------------------------------------------


async def test_reserve_then_settle_records_confirmed_cost_and_rate(
    g: Governance, ctx: RunContext, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await enable_paid(g, rate="100")
    r = await reserve(g, ctx, inr="40")
    assert (r.state, r.reserved_inr, r.usd_inr_rate, r.existing) == (
        "reserved",
        D("40.000000"),
        RATE,
        False,
    )
    assert (await g.spend.usage()).month_used_inr == 40  # a reservation counts immediately
    out = await g.spend.settle(r.id, confirmed_usd=D("0.25"))
    assert (out.state, out.confirmed_inr, out.overrun) == ("settled", D("25.000000"), False)
    assert (await g.spend.usage()).month_used_inr == 25  # settled at the real, lower amount
    async with sessions() as s:
        row = (await s.execute(select(SpendLedger))).scalar_one()
    assert (row.currency, D(row.usd_inr_rate), row.provider, row.model_name) == (
        "INR",
        RATE,
        "paidco",
        "paid-1",
    )


async def test_cost_is_rounded_up_never_down(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g, rate="90")
    r = await g.spend.reserve(
        execution_id=ctx.execution_id,
        user_id=ctx.user_id,
        agent_id=ctx.agent_id,
        provider="p",
        model_name="m",
        call_key="k",
        worst_case_usd=D("0.0000001"),
        run_limit_inr=D(10),
    )
    assert r.reserved_inr == D("0.000009")  # 0.000009 exactly; any fraction would round up


async def test_released_reservation_counts_nothing(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="50")
    await g.spend.release(r.id)
    assert (await g.spend.usage()).month_used_inr == 0


# --- per-run, daily and monthly limits ------------------------------------------------------------------


async def test_a_run_cannot_exceed_its_per_run_budget(
    g: Governance, ctx: RunContext, session: AsyncSession
) -> None:
    await enable_paid(g)
    await reserve(g, ctx, inr="30", run_limit="50")
    with pytest.raises(BudgetExceededError) as exc:
        await reserve(g, ctx, inr="30", run_limit="50")  # 60 > 50 for THIS run
    assert exc.value.scope == "run"
    await reserve(g, ctx, inr="20", run_limit="50")  # exactly at the limit is allowed
    with pytest.raises(BudgetExceededError):
        await reserve(g, ctx, inr="0.01", run_limit="50")
    other = await runnable_agent(
        session, g, "messaging_agent", user_id=ctx.user_id
    )  # a different run has its own budget
    await reserve(g, other, inr="30", run_limit="50")


async def test_zero_per_run_budget_blocks_all_paid_calls(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    with pytest.raises(BudgetExceededError, match="run"):
        await reserve(g, ctx, inr="0.01", run_limit="0")  # the default per-version limit is INR 0


async def test_daily_limit_and_rollover_at_ist_midnight(
    g: Governance, ctx: RunContext, clock: Clock
) -> None:
    await enable_paid(g, monthly="300", daily="100")
    await reserve(g, ctx, inr="60")
    await reserve(g, ctx, inr="40")
    with pytest.raises(BudgetExceededError) as exc:
        await reserve(g, ctx, inr="1")
    assert exc.value.scope == "day"
    clock.now = datetime(2026, 11, 23, 18, 29, 59, tzinfo=UTC)  # 23:59:59 IST: still the same day
    with pytest.raises(BudgetExceededError):
        await reserve(g, ctx, inr="1")
    clock.now = datetime(2026, 11, 23, 18, 30, 0, tzinfo=UTC)  # 00:00:00 IST: a new day
    await reserve(g, ctx, inr="100")
    usage = await g.spend.usage()
    assert (usage.day.isoformat(), usage.day_used_inr, usage.month_used_inr) == (
        "2026-11-24",
        D(100),
        D(200),
    )


async def test_monthly_limit_and_rollover(g: Governance, ctx: RunContext, clock: Clock) -> None:
    await enable_paid(g, monthly="300", daily="100")
    for day in (26, 27, 28):  # INR 100 on each of three days: the daily limit never binds
        clock.now = datetime(2026, 11, day, 6, 30, tzinfo=UTC)
        await reserve(g, ctx, inr="100")
    clock.now = datetime(2026, 11, 30, 6, 30, tzinfo=UTC)  # a fresh day, but the month is spent
    with pytest.raises(BudgetExceededError) as exc:
        await reserve(g, ctx, inr="0.01")
    assert exc.value.scope == "month"
    clock.now = datetime(
        2026, 11, 30, 18, 29, 59, tzinfo=UTC
    )  # 23:59:59 IST, 30 Nov: still November
    with pytest.raises(BudgetExceededError, match="month"):
        await reserve(g, ctx, inr="1")
    clock.now = datetime(2026, 11, 30, 18, 30, 0, tzinfo=UTC)  # 00:00 IST, 1 Dec: a fresh month
    await reserve(g, ctx, inr="100")
    usage = await g.spend.usage()
    assert (usage.month.isoformat(), usage.month_used_inr) == ("2026-12-01", D(100))
    assert await counted(g.factory) == 400  # November's rows stay as history


async def test_monthly_total_can_never_exceed_300_even_if_config_were_raised_in_db(
    g: Governance, ctx: RunContext, session: AsyncSession
) -> None:
    await enable_paid(g, monthly="300", daily="300")
    for constraint in ("monthly_ceiling", "daily_ceiling", "daily_le_monthly"):
        await session.execute(
            text(f"ALTER TABLE spending_config DROP CONSTRAINT ck_spending_config_{constraint}")
        )
    await session.execute(
        text("UPDATE spending_config SET monthly_budget_inr = 5000, daily_budget_inr = 5000")
    )
    await session.commit()
    for _ in range(3):
        await reserve(g, ctx, inr="100")
    with pytest.raises(BudgetExceededError):  # the in-code ceiling still holds
        await reserve(g, ctx, inr="1")


# --- concurrency -------------------------------------------------------------------------------------------


async def test_concurrent_reservations_cannot_overspend_the_monthly_budget(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g, monthly="300", daily="300")
    results = await asyncio.gather(
        *(reserve(g, ctx, inr="40") for _ in range(12)), return_exceptions=True
    )
    ok = [r for r in results if isinstance(r, spending.Reservation)]
    refused = [r for r in results if isinstance(r, BudgetExceededError)]
    assert len(ok) == 7 and len(refused) == 5  # 7 x 40 = 280 fits; an 8th (320) does not
    assert not [
        r for r in results if isinstance(r, Exception) and not isinstance(r, BudgetExceededError)
    ]
    assert (await g.spend.usage()).month_used_inr == 280 <= 300
    assert await counted(g.factory) == 280


async def test_concurrent_reserve_and_settle_stay_consistent(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g, monthly="300", daily="300")
    first = [await reserve(g, ctx, inr="40") for _ in range(5)]
    tasks: list[Any] = [
        g.spend.settle(r.id, confirmed_usd=D("0.1")) for r in first
    ]  # settle at INR 10 each, concurrently ...
    tasks += [reserve(g, ctx, inr="40") for _ in range(10)]  # ... while others keep reserving
    await asyncio.gather(*tasks, return_exceptions=True)
    used = (await g.spend.usage()).month_used_inr
    assert used <= 300 and used == await counted(g.factory)


# --- idempotency: retries cannot double charge ----------------------------------------------------------------


async def test_same_call_key_reserves_once(
    g: Governance, ctx: RunContext, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await enable_paid(g)
    a = await reserve(g, ctx, key="exec:1:0", inr="40")
    b = await reserve(g, ctx, key="exec:1:0", inr="40")  # a retry/replay of the same attempt
    assert (a.id, a.existing, b.id, b.existing) == (a.id, False, a.id, True)
    assert (await g.spend.usage()).month_used_inr == 40  # NOT 80
    async with sessions() as s:
        assert len((await s.execute(select(SpendLedger))).scalars().all()) == 1


async def test_concurrent_identical_requests_create_one_hold(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g)
    results = await asyncio.gather(*(reserve(g, ctx, key="same-key", inr="25") for _ in range(8)))
    assert len({r.id for r in results}) == 1 and sum(not r.existing for r in results) == 1
    assert (await g.spend.usage()).month_used_inr == 25


async def test_settling_twice_never_double_charges(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="50")
    first = await g.spend.settle(r.id, confirmed_usd=D("0.30"))
    again = await g.spend.settle(r.id, confirmed_usd=D("0.30"))
    different = await g.spend.settle(
        r.id, confirmed_usd=D("0.45")
    )  # a late/duplicate report cannot rewrite it
    assert first.confirmed_inr == again.confirmed_inr == different.confirmed_inr == D("30.000000")
    assert (await g.spend.usage()).month_used_inr == 30


async def test_release_and_settle_are_final_and_mutually_exclusive(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g)
    a = await reserve(g, ctx, inr="10")
    await g.spend.settle(a.id, confirmed_usd=D("0.1"))
    await g.spend.release(a.id)  # cannot un-charge a settled call
    assert (await g.spend.settle(a.id, confirmed_usd=D(0))).state == "settled"
    b = await reserve(g, ctx, inr="10")
    await g.spend.release(b.id)
    assert (
        await g.spend.settle(b.id, confirmed_usd=D("0.1"))
    ).state == "released"  # cannot charge a released one
    assert (await g.spend.usage()).month_used_inr == 10


# --- unknown cost, possible charges, overruns: fail closed ----------------------------------------------------------


async def test_unknown_cost_is_held_at_worst_case_and_freezes_paid(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="40")
    out = await g.spend.settle(r.id, confirmed_usd=None)  # provider did not tell us the cost
    assert (out.state, out.confirmed_inr) == ("held", None)
    usage = await g.spend.usage()
    assert usage.month_used_inr == 40 and usage.held_inr == 40  # NOT zero
    snap = await g.spend.snapshot()
    assert snap.paid_frozen and "could not be determined" in (snap.paid_frozen_reason or "")
    with pytest.raises(PaidSpendingBlockedError, match="frozen"):
        await reserve(g, ctx, inr="1")


async def test_possibly_charged_failure_holds_and_freezes_but_not_charged_failure_releases(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g)
    rejected = await reserve(g, ctx, inr="20")
    await g.spend.release(rejected.id, "rejected before processing")
    assert (
        not (await g.spend.snapshot()).paid_frozen and (await g.spend.usage()).month_used_inr == 0
    )
    timeout = await reserve(g, ctx, inr="20")
    await g.spend.hold(timeout.id, "provider timeout")
    assert (await g.spend.snapshot()).paid_frozen and (await g.spend.usage()).held_inr == 20


async def test_actual_cost_above_reservation_is_recorded_in_full_and_freezes(
    g: Governance, ctx: RunContext
) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="10")
    out = await g.spend.settle(
        r.id, confirmed_usd=D("0.25")
    )  # provider charged INR 25 > 10 reserved
    assert (out.state, out.confirmed_inr, out.overrun) == ("settled", D("25.000000"), True)
    assert (await g.spend.usage()).month_used_inr == 25  # the real amount, never understated
    assert (await g.spend.snapshot()).paid_frozen


async def test_negative_reported_cost_is_treated_as_unknown(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="10")
    assert (await g.spend.settle(r.id, confirmed_usd=D("-1"))).state == "held"


async def test_operator_resolves_held_entries_and_unfreezes(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    a, b = await reserve(g, ctx, inr="40"), await reserve(g, ctx, inr="30")
    await g.spend.settle(a.id, confirmed_usd=None)
    await g.spend.settle(b.id, confirmed_usd=None)
    await spending.resolve_held(g.factory, ADMIN, a.id, D("12.5"))  # invoice says INR 12.50
    await spending.resolve_held(g.factory, ADMIN, b.id, None)  # provider confirms no charge
    usage = await g.spend.usage()
    assert (usage.month_used_inr, usage.held_inr) == (D("12.5"), D(0))
    assert (await g.spend.snapshot()).paid_frozen  # resolving does not unfreeze by itself
    with pytest.raises(ValueError, match="no held"):
        await spending.resolve_held(g.factory, ADMIN, a.id, D(1))  # already final
    await spending.unfreeze(g.factory, ADMIN, "reconciled with the invoice")
    await reserve(g, ctx, inr="5")


# --- operator-only and audit ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "action",
    [
        lambda g, a: spending.set_budget(g.factory, a, D(10), D(5)),
        lambda g, a: spending.set_rate(g.factory, a, D(90), "x"),
        lambda g, a: spending.set_paid_models_enabled(g.factory, a, True),
        lambda g, a: spending.unfreeze(g.factory, a, "x"),
        lambda g, a: spending.resolve_held(g.factory, a, uuid.uuid4(), None),
    ],
)
async def test_app_actors_cannot_change_spending_and_the_attempt_is_audited(
    g: Governance, sessions: async_sessionmaker[AsyncSession], action: object
) -> None:
    user = Actor.user(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(PermissionDeniedError, match="operator-only"):
        await action(g, user)  # type: ignore[operator]
    snap = await g.spend.snapshot()
    assert (snap.monthly_budget_inr, snap.paid_models_enabled, snap.usd_inr_rate) == (
        D(0),
        False,
        None,
    )
    async with sessions() as s:
        events = (
            (
                await s.execute(
                    select(AuditEvent).where(
                        AuditEvent.event_type == "governance.privileged_action_denied"
                    )
                )
            )
            .scalars()
            .all()
        )
    assert (
        len(events) == 1
        and events[0].actor_type == "user"
        and events[0].actor_user_id == user.user_id
    )


async def test_every_config_change_is_audited_with_old_values(
    g: Governance, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await spending.set_budget(g.factory, ADMIN, D(100), D(40))
    await spending.set_budget(g.factory, ADMIN, D(200), D(80))
    async with sessions() as s:
        ev = (
            (
                await s.execute(
                    select(AuditEvent)
                    .where(AuditEvent.event_type == "spending.budget_set")
                    .order_by(AuditEvent.id)
                )
            )
            .scalars()
            .all()
        )
    assert [e.detail["monthly"] for e in ev] == ["100", "200"] and ev[1].detail[
        "old_monthly"
    ] == "100.0000"


# --- ledger integrity (triggers) ---------------------------------------------------------------------------------------


async def test_settled_and_released_rows_are_immutable_and_nothing_is_deletable(
    g: Governance, ctx: RunContext, session: AsyncSession
) -> None:
    await enable_paid(g)
    a, b, c = [await reserve(g, ctx, inr="10") for _ in range(3)]
    await g.spend.settle(a.id, confirmed_usd=D("0.05"))
    await g.spend.release(b.id)
    await session.commit()
    for sql in (
        f"UPDATE spend_ledger SET confirmed_inr = 0 WHERE id = '{a.id}'",
        f"UPDATE spend_ledger SET reserved_inr = 0 WHERE id = '{b.id}'",
        f"UPDATE spend_ledger SET state = 'reserved', confirmed_inr = NULL WHERE id = '{a.id}'",
        "DELETE FROM spend_ledger",
        "TRUNCATE spend_ledger",
        "DELETE FROM spending_config",
        "DELETE FROM kill_switch_state",
    ):
        async with session.begin_nested() as sp:
            with pytest.raises(DBAPIError, match="final|cannot be deleted"):
                await session.execute(text(sql))
            await sp.rollback()
    await g.spend.settle(c.id, confirmed_usd=D("0.01"))  # a live row can still progress


async def test_confirmed_amount_exists_only_on_settled_rows(
    g: Governance, ctx: RunContext, session: AsyncSession
) -> None:
    await enable_paid(g)
    r = await reserve(g, ctx, inr="10")
    await session.commit()
    async with session.begin_nested() as sp:
        with pytest.raises((IntegrityError, DBAPIError)):
            await session.execute(
                text(f"UPDATE spend_ledger SET confirmed_inr = 5 WHERE id = '{r.id}'")
            )
        await sp.rollback()


# --- fail closed when state is unreadable -----------------------------------------------------------------------------------


async def test_missing_spending_config_fails_closed(
    g: Governance, ctx: RunContext, session: AsyncSession
) -> None:
    await enable_paid(g)
    await session.execute(
        text("ALTER TABLE spending_config DISABLE TRIGGER spending_config_no_delete")
    )
    await session.execute(text("DELETE FROM spending_config"))
    await session.commit()
    with pytest.raises(GovernanceUnavailableError):
        await g.spend.snapshot()
    with pytest.raises(GovernanceUnavailableError):
        await reserve(g, ctx)
    with pytest.raises(GovernanceError):
        await g.policy()


async def test_kill_switch_blocks_new_reservations(g: Governance, ctx: RunContext) -> None:
    await enable_paid(g)
    await killswitch.activate(g.factory, ADMIN, "test")
    with pytest.raises(KillSwitchActiveError):
        await reserve(g, ctx)
    assert await counted(g.factory) == 0  # nothing was held
