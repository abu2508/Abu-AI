"""Pure governance rules: no database."""

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from core.governance.executions import safe_reason
from core.governance.spending import SpendingSnapshot, ceil6, periods
from core.model_router.gateway import determine_cost
from core.model_router.router import ResolvedModel
from core.model_router.types import CompletionResult
from database.schemas import ABSOLUTE_MONTHLY_CEILING_INR

NOW = datetime(2026, 11, 23, 12, 0, tzinfo=UTC)
FREE = ResolvedModel("f", "p", "pid", "gw", None)
PAID = ResolvedModel("m", "p", "pid", "gw", None, True, Decimal("2"), Decimal("10"))


def result(**kw: object) -> CompletionResult:
    base: dict[str, object] = {
        "text": "x",
        "input_tokens": 1000,
        "output_tokens": 500,
        "model_name": "m",
        "provider": "p",
        "latency_ms": 1.0,
    }
    base.update(kw)
    return CompletionResult(**base)


def snap(**kw: object) -> SpendingSnapshot:
    base: dict[str, object] = {
        "monthly_budget_inr": Decimal(100),
        "daily_budget_inr": Decimal(50),
        "paid_models_enabled": True,
        "paid_frozen": False,
        "paid_frozen_reason": None,
        "usd_inr_rate": Decimal("90"),
        "rate_source": "t",
        "rate_set_at": NOW - timedelta(days=1),
        "rate_max_age_days": 14,
        "utc_offset_minutes": 330,
    }
    base.update(kw)
    return SpendingSnapshot(**base)  # type: ignore[arg-type]


def test_absolute_ceiling_is_300_inr() -> None:
    assert Decimal(300) == ABSOLUTE_MONTHLY_CEILING_INR


def test_rounding_always_goes_up() -> None:
    assert ceil6(Decimal("0.0000011")) == Decimal("0.000002")
    assert ceil6(Decimal("1.0000000001")) == Decimal("1.000001")
    assert ceil6(Decimal("2")) == Decimal("2.000000")


def test_period_boundaries_follow_ist_midnight() -> None:
    before = datetime(2026, 11, 23, 18, 29, 59, tzinfo=UTC)  # 23:59:59 IST on the 23rd
    after = datetime(2026, 11, 23, 18, 30, 0, tzinfo=UTC)  # 00:00:00 IST on the 24th
    assert periods(before, 330)[0].isoformat() == "2026-11-23"
    assert periods(after, 330)[0].isoformat() == "2026-11-24"
    nov_end = datetime(2026, 11, 30, 18, 29, 59, tzinfo=UTC)
    dec_start = datetime(2026, 11, 30, 18, 30, 0, tzinfo=UTC)
    assert periods(nov_end, 330)[1].isoformat() == "2026-11-01"
    assert periods(dec_start, 330)[1].isoformat() == "2026-12-01"
    assert periods(NOW, 0)[0].isoformat() == "2026-11-23"  # configurable offset


@pytest.mark.parametrize(
    ("kw", "needle"),
    [
        ({"paid_models_enabled": False}, "disabled"),
        ({"paid_frozen": True, "paid_frozen_reason": "unknown cost"}, "frozen"),
        ({"monthly_budget_inr": Decimal(0)}, "zero"),
        ({"daily_budget_inr": Decimal(0)}, "zero"),
        ({"usd_inr_rate": None}, "not set"),
        ({"rate_set_at": NOW - timedelta(days=15)}, "stale"),
    ],
)
def test_paid_is_blocked_unless_every_condition_holds(kw: dict[str, object], needle: str) -> None:
    reason = snap(**kw).paid_block_reason(NOW)
    assert reason is not None and needle in reason


def test_paid_allowed_only_when_fully_configured() -> None:
    assert snap().paid_block_reason(NOW) is None


def test_free_models_cost_zero_and_never_unknown() -> None:
    out = determine_cost(result(usage_reported=False, input_tokens=0, output_tokens=0), FREE)
    assert out.usd == 0


def test_paid_cost_from_registry_price_and_reported_usage() -> None:
    out = determine_cost(result(), PAID)
    assert out.usd == Decimal("0.007") and out.source == "registry_price"  # (1000*2 + 500*10) / 1e6


def test_gateway_reported_cost_is_preferred_and_not_added_to_registry_cost() -> None:
    out = determine_cost(result(provider_cost_usd=Decimal("0.004")), PAID)
    assert (
        out.usd == Decimal("0.004") and out.source == "gateway_reported"
    )  # one figure, never a sum


def test_unknown_cost_is_never_assumed_zero() -> None:
    out = determine_cost(result(usage_reported=False, input_tokens=0, output_tokens=0), PAID)
    assert out.usd is None and out.source is None


def test_negative_gateway_cost_is_not_trusted() -> None:
    out = determine_cost(result(provider_cost_usd=Decimal("-1"), usage_reported=False), PAID)
    assert out.usd is None


@pytest.mark.parametrize(
    "reason", ["timeout", "provider_rejected", "budget_exceeded_month", "a.b:c-d_e"]
)
def test_failure_reasons_are_machine_codes(reason: str) -> None:
    assert safe_reason(reason) == reason


@pytest.mark.parametrize(
    "reason", ["user said: my salary is 5000", "x" * 80, "", "Has Caps", "a b"]
)
def test_free_text_failure_reasons_are_replaced(reason: str) -> None:
    assert safe_reason(reason) == "unspecified"


def test_trace_ids_are_unique_hex() -> None:
    assert len({uuid.uuid4().hex for _ in range(100)}) == 100
