"""Pure routing-policy rules, no database needed."""

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from core.model_registry.service import approval_blockers
from core.model_router.router import rejection_reason
from database.schemas import (
    ApprovalStatus,
    DataClass,
    ModelCapability,
    ModelPricing,
    ProviderPolicy,
    RegisteredModel,
    Tri,
)

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def make(
    *,
    approved: bool = True,
    enabled: bool = True,
    price: str | None = "0",
    verified: bool = True,
    max_class: DataClass = DataClass.SYNTHETIC,
    caps: tuple[str, ...] = ("text_generation",),
    availability: bool = True,
) -> RegisteredModel:
    policy = ProviderPolicy(
        provider="p",
        retention_summary="x",
        trains_on_data=Tri.NO,
        human_review=Tri.NO,
        max_data_class=max_class,
        source_url="https://example.test" if verified else None,
        verified_at=NOW if verified else None,
    )
    return RegisteredModel(
        name="m",
        provider_model_id="exact-id",
        gateway_model_name="gw",
        approval_status=ApprovalStatus.APPROVED if approved else ApprovalStatus.PENDING,
        enabled=enabled,
        availability_verified_at=NOW if availability else None,
        policy=policy,
        capabilities=[ModelCapability(capability=c) for c in caps],
        pricing=None
        if price is None
        else ModelPricing(
            input_per_million=Decimal(price), output_per_million=Decimal(price), verified_at=NOW
        ),
    )


def test_eligible_free_model() -> None:
    assert rejection_reason(make(), DataClass.SYNTHETIC) is None


@pytest.mark.parametrize(
    ("kwargs", "needle"),
    [
        ({"approved": False}, "not approved"),
        ({"enabled": False}, "disabled"),
        ({"price": None}, "pricing unknown"),
        ({"price": "0.25"}, "paid"),
        ({"verified": False}, "not verified"),
    ],
)
def test_ineligible_models(kwargs: dict[str, object], needle: str) -> None:
    reason = rejection_reason(make(**kwargs), DataClass.SYNTHETIC)  # type: ignore[arg-type]
    assert reason is not None and needle in reason


def test_data_class_ceiling_enforced() -> None:
    m = make(max_class=DataClass.SYNTHETIC)
    assert rejection_reason(m, DataClass.NON_SENSITIVE) is not None
    assert rejection_reason(m, DataClass.SENSITIVE) is not None
    m2 = make(max_class=DataClass.SENSITIVE)
    assert rejection_reason(m2, DataClass.SENSITIVE) is None


def test_required_capability() -> None:
    assert rejection_reason(make(), DataClass.SYNTHETIC, ["vision"]) is not None
    assert rejection_reason(make(caps=("vision",)), DataClass.SYNTHETIC, ["vision"]) is None


def test_approval_blockers_listed() -> None:
    m = make(price=None, verified=False, availability=False)
    blockers = approval_blockers(m)
    assert len(blockers) == 3


def test_approvable_when_complete() -> None:
    assert approval_blockers(make()) == []
