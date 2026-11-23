"""The authoritative paid-spend controller.

Concurrency: reserve() runs in its OWN committed transaction and takes SELECT ... FOR UPDATE on the
single spending_config row, so every paid reservation is serialised and sees all earlier committed
holds. The provider is called only after the reservation is committed.

Counting (per period): settled -> confirmed cost; reserved/held -> the worst-case reservation;
released -> 0. A held row (cost unknown / possibly charged) is therefore never counted as zero.

Fail-closed: unknown cost, possible charge, overrun, missing/stale rate, unreadable state => no
further paid spending until an operator resolves it. Free models never touch this module.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_CEILING, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import SYSTEM, Actor, record_event, record_event_durable
from core.governance.errors import (
    BudgetExceededError,
    GovernanceUnavailableError,
    PaidSpendingBlockedError,
)
from core.governance.killswitch import check_inactive_in_tx
from core.permissions.errors import PermissionDeniedError
from database.schemas import (
    ABSOLUTE_MONTHLY_CEILING_INR,
    KillSwitchState,
    SpendingConfig,
    SpendLedger,
)

Factory = async_sessionmaker[AsyncSession]
Clock = Callable[[], datetime]
_SIX = Decimal("0.000001")
ZERO = Decimal(0)


def ceil6(x: Decimal) -> Decimal:
    """Always round UP: a cost estimate must never be understated."""
    return x.quantize(_SIX, rounding=ROUND_CEILING)


def periods(now_utc: datetime, offset_minutes: int) -> tuple[date, date]:
    """(local day, first day of local month) for the configured UTC offset."""
    local = (now_utc.astimezone(UTC) + timedelta(minutes=offset_minutes)).date()
    return local, local.replace(day=1)


_COUNTED = case(
    (SpendLedger.state == "settled", SpendLedger.confirmed_inr),
    (SpendLedger.state == "released", ZERO),
    else_=SpendLedger.reserved_inr,
)


async def _used(s: AsyncSession, *where: Any) -> Decimal:
    value = (
        await s.execute(select(func.coalesce(func.sum(_COUNTED), 0)).where(*where))
    ).scalar_one()
    return Decimal(value)


@dataclass(frozen=True)
class SpendingSnapshot:
    monthly_budget_inr: Decimal
    daily_budget_inr: Decimal
    paid_models_enabled: bool
    paid_frozen: bool
    paid_frozen_reason: str | None
    usd_inr_rate: Decimal | None
    rate_source: str | None
    rate_set_at: datetime | None
    rate_max_age_days: int
    utc_offset_minutes: int

    def paid_block_reason(self, now: datetime) -> str | None:
        if not self.paid_models_enabled:
            return "paid models are disabled"
        if self.paid_frozen:
            return (
                f"paid spending is frozen: {self.paid_frozen_reason or 'operator action required'}"
            )
        if self.monthly_budget_inr <= 0 or self.daily_budget_inr <= 0:
            return "paid budget is zero"
        if self.usd_inr_rate is None or self.rate_set_at is None:
            return "USD-INR rate is not set"
        if now - self.rate_set_at > timedelta(days=self.rate_max_age_days):
            return "USD-INR rate is stale"
        return None


def _cfg_snapshot(c: SpendingConfig) -> SpendingSnapshot:
    return SpendingSnapshot(
        Decimal(c.monthly_budget_inr),
        Decimal(c.daily_budget_inr),
        c.paid_models_enabled,
        c.paid_frozen,
        c.paid_frozen_reason,
        Decimal(c.usd_inr_rate) if c.usd_inr_rate is not None else None,
        c.rate_source,
        c.rate_set_at,
        c.rate_max_age_days,
        c.utc_offset_minutes,
    )


@dataclass(frozen=True)
class Reservation:
    id: uuid.UUID
    state: str
    reserved_inr: Decimal
    usd_inr_rate: Decimal
    existing: (
        bool  # True: this call_key was already reserved (a replay) - do NOT call the provider again
    )


@dataclass(frozen=True)
class Settlement:
    state: str  # settled | held | released
    confirmed_inr: Decimal | None
    reserved_inr: Decimal
    overrun: bool = False


@dataclass(frozen=True)
class Usage:
    day: date
    month: date
    day_used_inr: Decimal
    month_used_inr: Decimal
    held_inr: Decimal  # unresolved possibly-charged amounts (included in the figures above)


class SpendController:
    def __init__(self, factory: Factory, clock: Clock | None = None) -> None:
        self._factory = factory
        self._clock: Clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ reads
    async def snapshot(self) -> SpendingSnapshot:
        try:
            async with self._factory() as s:
                row = (
                    await s.execute(select(SpendingConfig).where(SpendingConfig.id == 1))
                ).scalar_one_or_none()
        except Exception as exc:
            raise GovernanceUnavailableError("spending configuration could not be read") from exc
        if row is None:
            raise GovernanceUnavailableError("spending configuration row is missing")
        return _cfg_snapshot(row)

    async def usage(self) -> Usage:
        snap = await self.snapshot()
        day, month = periods(self._clock(), snap.utc_offset_minutes)
        async with self._factory() as s:
            day_used = await _used(s, SpendLedger.period_day == day)
            month_used = await _used(s, SpendLedger.period_month == month)
            held = await _used(s, SpendLedger.period_month == month, SpendLedger.state == "held")
        return Usage(day, month, day_used, month_used, held)

    # --------------------------------------------------------------- reserve
    async def reserve(
        self,
        *,
        execution_id: uuid.UUID,
        user_id: uuid.UUID,
        agent_id: str,
        provider: str,
        model_name: str,
        call_key: str,
        worst_case_usd: Decimal,
        run_limit_inr: Decimal,
    ) -> Reservation:
        now = self._clock()
        async with self._factory() as s, s.begin():
            cfg = (
                await s.execute(
                    select(SpendingConfig).where(SpendingConfig.id == 1).with_for_update()
                )
            ).scalar_one_or_none()  # the lock that serialises every paid reservation
            if cfg is None:
                raise GovernanceUnavailableError("spending configuration row is missing")
            check_inactive_in_tx(
                (
                    await s.execute(select(KillSwitchState).where(KillSwitchState.id == 1))
                ).scalar_one_or_none()
            )

            existing = (
                await s.execute(select(SpendLedger).where(SpendLedger.call_key == call_key))
            ).scalar_one_or_none()
            if existing is not None:  # idempotent: a replay never creates a second hold or charge
                return Reservation(
                    existing.id,
                    existing.state,
                    Decimal(existing.reserved_inr),
                    Decimal(existing.usd_inr_rate),
                    True,
                )

            snap = _cfg_snapshot(cfg)
            blocked = snap.paid_block_reason(now)
            if blocked or snap.usd_inr_rate is None:
                raise PaidSpendingBlockedError(blocked or "USD-INR rate is not set")
            if worst_case_usd < 0:
                raise PaidSpendingBlockedError("invalid cost estimate")
            amount = ceil6(worst_case_usd * snap.usd_inr_rate)
            day, month = periods(now, snap.utc_offset_minutes)

            run_used = await _used(s, SpendLedger.execution_id == execution_id)
            if run_used + amount > run_limit_inr:
                raise BudgetExceededError("run", str(run_limit_inr), str(run_used), str(amount))
            day_used = await _used(s, SpendLedger.period_day == day)
            if day_used + amount > snap.daily_budget_inr:
                raise BudgetExceededError(
                    "day", str(snap.daily_budget_inr), str(day_used), str(amount)
                )
            month_used = await _used(s, SpendLedger.period_month == month)
            # The absolute ceiling is re-asserted here as well as in the DB CHECK: belt and braces.
            month_limit = min(snap.monthly_budget_inr, ABSOLUTE_MONTHLY_CEILING_INR)
            if month_used + amount > month_limit:
                raise BudgetExceededError("month", str(month_limit), str(month_used), str(amount))

            row = SpendLedger(
                call_key=call_key,
                execution_id=execution_id,
                user_id=user_id,
                agent_id=agent_id,
                provider=provider,
                model_name=model_name,
                state="reserved",
                reserved_inr=amount,
                usd_inr_rate=snap.usd_inr_rate,
                period_day=day,
                period_month=month,
            )
            s.add(row)
            await s.flush()
            return Reservation(row.id, "reserved", amount, snap.usd_inr_rate, False)

    # ------------------------------------------------------- settle/release/hold
    async def _locked(self, s: AsyncSession, reservation_id: uuid.UUID) -> SpendLedger:
        await s.execute(
            select(SpendingConfig).where(SpendingConfig.id == 1).with_for_update()
        )  # lock order: config first
        row = (
            await s.execute(
                select(SpendLedger).where(SpendLedger.id == reservation_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise GovernanceUnavailableError("spend ledger row not found")
        return row

    async def settle(
        self, reservation_id: uuid.UUID, *, confirmed_usd: Decimal | None
    ) -> Settlement:
        """Idempotent: settling a row that is already final changes nothing (no double charge).
        confirmed_usd=None means the provider did not tell us the cost: hold + freeze (fail closed)."""
        async with self._factory() as s, s.begin():
            row = await self._locked(s, reservation_id)
            if row.state in ("settled", "released"):
                return Settlement(
                    row.state,
                    Decimal(row.confirmed_inr) if row.confirmed_inr is not None else None,
                    Decimal(row.reserved_inr),
                )
            if confirmed_usd is None or confirmed_usd < 0:
                row.state = "held"
                row.note = "cost unknown: held at worst case"
                await self._freeze(s, "provider cost could not be determined")
                return Settlement("held", None, Decimal(row.reserved_inr))
            confirmed = ceil6(confirmed_usd * Decimal(row.usd_inr_rate))
            overrun = confirmed > Decimal(row.reserved_inr)
            row.state = "settled"
            row.confirmed_inr = confirmed
            row.settled_at = self._clock()
            if overrun:  # the provider charged more than the worst case we reserved
                row.note = "actual cost exceeded reservation"
                await self._freeze(s, "actual cost exceeded the reservation")
            return Settlement("settled", confirmed, Decimal(row.reserved_inr), overrun)

    async def release(self, reservation_id: uuid.UUID, note: str = "not charged") -> None:
        """The provider definitely did not charge (request never sent / rejected)."""
        async with self._factory() as s, s.begin():
            row = await self._locked(s, reservation_id)
            if row.state == "reserved":
                row.state = "released"
                row.settled_at = self._clock()
                row.note = note[:200]

    async def hold(self, reservation_id: uuid.UUID, note: str) -> None:
        """The provider MAY have charged (timeout / 5xx after send): keep the worst case counted
        and freeze paid spending until an operator resolves it."""
        async with self._factory() as s, s.begin():
            row = await self._locked(s, reservation_id)
            if row.state == "reserved":
                row.state = "held"
                row.note = note[:200]
                await self._freeze(s, "a paid call may have been charged (cost unknown)")

    async def _freeze(self, s: AsyncSession, reason: str) -> None:
        cfg = (await s.execute(select(SpendingConfig).where(SpendingConfig.id == 1))).scalar_one()
        if not cfg.paid_frozen:
            cfg.paid_frozen = True
            cfg.paid_frozen_reason = reason[:200]
            cfg.paid_frozen_at = self._clock()
            cfg.updated_at = cfg.paid_frozen_at
            await record_event(
                s, SYSTEM, "spending.paid_frozen", "governance", "spending", {"reason": reason}
            )


# ----------------------------------------------------------------------- operator-only actions


async def _admin_only(factory: Factory, actor: Actor, action: str) -> None:
    if actor.type != "admin":
        await record_event_durable(
            factory,
            actor,
            "governance.privileged_action_denied",
            "governance",
            action,
            {"action": action},
        )
        raise PermissionDeniedError(f"{action} is operator-only")


async def _config_tx(factory: Factory) -> tuple[AsyncSession, SpendingConfig]:
    s = factory()
    await s.begin()
    cfg = (
        await s.execute(select(SpendingConfig).where(SpendingConfig.id == 1).with_for_update())
    ).scalar_one_or_none()
    if cfg is None:
        await s.rollback()
        await s.close()
        raise GovernanceUnavailableError("spending configuration row is missing")
    return s, cfg


async def set_budget(
    factory: Factory, actor: Actor, monthly_inr: Decimal, daily_inr: Decimal
) -> None:
    """Operator-only. Hard ceiling INR 300/month (also a DB CHECK). Rejections are audited."""
    await _admin_only(factory, actor, "spending.set_budget")
    problem = (
        f"monthly budget must be between 0 and {ABSOLUTE_MONTHLY_CEILING_INR} INR"
        if not (0 <= monthly_inr <= ABSOLUTE_MONTHLY_CEILING_INR)
        else "daily budget must be between 0 and the monthly budget"
        if not (0 <= daily_inr <= monthly_inr)
        else None
    )
    if problem:
        await record_event_durable(
            factory,
            actor,
            "spending.config_rejected",
            "governance",
            "spending",
            {"monthly": str(monthly_inr), "daily": str(daily_inr), "problem": problem},
        )
        raise ValueError(problem)
    s, cfg = await _config_tx(factory)
    try:
        old = (str(cfg.monthly_budget_inr), str(cfg.daily_budget_inr))
        cfg.monthly_budget_inr, cfg.daily_budget_inr = monthly_inr, daily_inr
        cfg.updated_at = datetime.now(UTC)
        await record_event(
            s,
            actor,
            "spending.budget_set",
            "governance",
            "spending",
            {
                "monthly": str(monthly_inr),
                "daily": str(daily_inr),
                "old_monthly": old[0],
                "old_daily": old[1],
            },
        )
        await s.commit()
    finally:
        await s.close()


async def set_rate(factory: Factory, actor: Actor, rate: Decimal, source: str) -> None:
    await _admin_only(factory, actor, "spending.set_rate")
    if not (Decimal(1) <= rate <= Decimal(1000)) or not source.strip():
        raise ValueError("rate must be between 1 and 1000 INR per USD and a source must be named")
    s, cfg = await _config_tx(factory)
    try:
        cfg.usd_inr_rate, cfg.rate_source, cfg.rate_set_at = (
            rate,
            source.strip()[:128],
            datetime.now(UTC),
        )
        cfg.updated_at = cfg.rate_set_at
        await record_event(
            s,
            actor,
            "spending.rate_set",
            "governance",
            "spending",
            {"rate": str(rate), "source": source[:128]},
        )
        await s.commit()
    finally:
        await s.close()


async def set_paid_models_enabled(factory: Factory, actor: Actor, enabled: bool) -> None:
    await _admin_only(factory, actor, "spending.set_paid_models_enabled")
    s, cfg = await _config_tx(factory)
    try:
        if enabled and (cfg.monthly_budget_inr <= 0 or cfg.usd_inr_rate is None):
            raise ValueError(
                "set a monthly budget above 0 and a USD-INR rate before enabling paid models"
            )
        cfg.paid_models_enabled = enabled
        cfg.updated_at = datetime.now(UTC)
        await record_event(
            s,
            actor,
            "spending.paid_models_enabled" if enabled else "spending.paid_models_disabled",
            "governance",
            "spending",
        )
        await s.commit()
    finally:
        await s.close()


async def unfreeze(factory: Factory, actor: Actor, reason: str) -> None:
    await _admin_only(factory, actor, "spending.unfreeze")
    if not reason.strip():
        raise ValueError("a reason is required")
    s, cfg = await _config_tx(factory)
    try:
        cfg.paid_frozen, cfg.paid_frozen_reason, cfg.paid_frozen_at = False, None, None
        cfg.updated_at = datetime.now(UTC)
        await record_event(
            s, actor, "spending.unfrozen", "governance", "spending", {"reason": reason}
        )
        await s.commit()
    finally:
        await s.close()


async def resolve_held(
    factory: Factory, actor: Actor, reservation_id: uuid.UUID, confirmed_inr: Decimal | None
) -> None:
    """Operator reconciles a possibly-charged/unknown-cost call: record the real INR amount from the
    provider's invoice, or release it (None) if the provider confirms there was no charge."""
    await _admin_only(factory, actor, "spending.resolve_held")
    async with factory() as s, s.begin():
        await s.execute(select(SpendingConfig).where(SpendingConfig.id == 1).with_for_update())
        row = (
            await s.execute(
                select(SpendLedger).where(SpendLedger.id == reservation_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None or row.state != "held":
            raise ValueError("no held ledger entry with that id")
        if confirmed_inr is None:
            row.state, row.note = "released", "operator: confirmed not charged"
        else:
            if confirmed_inr < 0:
                raise ValueError("amount must be non-negative")
            row.state, row.confirmed_inr, row.note = (
                "settled",
                ceil6(confirmed_inr),
                "operator: reconciled from invoice",
            )
        row.settled_at = datetime.now(UTC)
        await record_event(
            s,
            actor,
            "spending.held_resolved",
            "spend_ledger",
            str(reservation_id),
            {
                "outcome": row.state,
                "confirmed_inr": str(confirmed_inr) if confirmed_inr is not None else "",
            },
        )
