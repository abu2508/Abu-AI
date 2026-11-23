from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.model_registry import service as registry
from core.model_registry.errors import ApprovalError
from core.model_registry.schemas import Capability, ModelIn, PolicyIn, PricingIn
from database.schemas import Agent, ApprovalStatus, DataClass, Tri
from database.seed.registry import seed_registry

NOW = datetime(2026, 11, 23, tzinfo=UTC)


def model_in(name: str = "m1", price: str = "0", **kw: object) -> ModelIn:
    return ModelIn(
        name=name,
        provider="prov",
        provider_model_id="exact-id",
        gateway_model_name="gw",
        capabilities=[Capability.TEXT_GENERATION],
        pricing=PricingIn(input_per_million=price, output_per_million=price, verified_at=NOW),
        availability_verified_at=NOW,
        **kw,
    )


@pytest.fixture
async def with_policy(session: AsyncSession) -> AsyncSession:
    await registry.register_policy(
        session,
        PolicyIn(
            provider="prov",
            retention_summary="r",
            trains_on_data=Tri.NO,
            human_review=Tri.NO,
            source_url="https://example.test",
            verified_at=NOW,
        ),
    )
    return session


async def test_seed_is_idempotent_and_registers_three_disabled_agents(
    session: AsyncSession,
) -> None:
    await seed_registry(session)
    await seed_registry(session)
    agents = (await session.execute(select(Agent).order_by(Agent.agent_id))).scalars().all()
    assert [a.agent_id for a in agents] == ["daily_coach_agent", "finance_agent", "messaging_agent"]
    assert not any(a.enabled for a in agents)


async def test_seeded_gemini_is_synthetic_only_and_disabled(session: AsyncSession) -> None:
    await seed_registry(session)
    m = await registry.get_model(session, "gemini-3.1-flash-lite-free")
    assert m.provider_model_id == "gemini-3.1-flash-lite"
    assert m.approval_status is ApprovalStatus.APPROVED
    assert m.enabled is False
    assert m.pricing is not None and m.pricing.is_free
    assert m.rate_limits_verified is False
    assert m.policy.max_data_class is DataClass.SYNTHETIC
    assert m.policy.trains_on_data is Tri.YES


@pytest.mark.parametrize("target", [DataClass.NON_SENSITIVE, DataClass.SENSITIVE])
async def test_cannot_raise_data_class_for_training_provider(
    session: AsyncSession, target: DataClass
) -> None:
    await seed_registry(session)
    with pytest.raises(ApprovalError):
        await registry.approve_data_class(session, "google-ai-studio-free", target, "abu")


async def test_sensitive_needs_documented_no_training(with_policy: AsyncSession) -> None:
    s = with_policy
    # unknown is not enough
    await registry.register_policy(
        s,
        PolicyIn(
            provider="vague", retention_summary="?", source_url="https://x.test", verified_at=NOW
        ),
    )
    with pytest.raises(ApprovalError):
        await registry.approve_data_class(s, "vague", DataClass.SENSITIVE, "abu")
    # needs a named approver
    with pytest.raises(ApprovalError):
        await registry.approve_data_class(s, "prov", DataClass.SENSITIVE, " ")
    p = await registry.approve_data_class(s, "prov", DataClass.SENSITIVE, "abu")
    assert p.max_data_class is DataClass.SENSITIVE


async def test_new_policy_starts_synthetic_only(with_policy: AsyncSession) -> None:
    assert (await registry.get_policy(with_policy, "prov")).max_data_class is DataClass.SYNTHETIC


async def test_approval_blocked_without_verified_availability(with_policy: AsyncSession) -> None:
    s = with_policy
    data = model_in()
    data.availability_verified_at = None
    await registry.register_model(s, data)
    with pytest.raises(ApprovalError, match="availability"):
        await registry.approve_model(s, "m1")


async def test_cannot_enable_unapproved(with_policy: AsyncSession) -> None:
    await registry.register_model(with_policy, model_in())
    with pytest.raises(ApprovalError, match="not approved"):
        await registry.set_enabled(with_policy, "m1", True)


async def test_paid_model_cannot_be_enabled_until_operator_opts_in(
    with_policy: AsyncSession,
) -> None:
    s = with_policy
    await registry.register_model(s, model_in("paid", price="1.5"))
    await registry.approve_model(s, "paid")
    with pytest.raises(ApprovalError, match="not opted in"):  # default: paid models off, budget 0
        await registry.set_enabled(s, "paid", True)
    await s.execute(
        text("UPDATE spending_config SET monthly_budget_inr = 10, daily_budget_inr = 5")
    )
    with pytest.raises(ApprovalError, match="not opted in"):  # a budget alone is not an opt-in
        await registry.set_enabled(s, "paid", True)
    await s.execute(text("UPDATE spending_config SET paid_models_enabled = true"))
    assert (await registry.set_enabled(s, "paid", True)).enabled is True


async def test_models_default_to_pending_and_disabled(with_policy: AsyncSession) -> None:
    m = await registry.register_model(with_policy, model_in())
    assert m.approval_status is ApprovalStatus.PENDING and m.enabled is False


async def test_enabled_state_survives_raw_sql_check(session: AsyncSession) -> None:
    await seed_registry(session)
    with pytest.raises(Exception, match="enabled_requires_approval"):
        await session.execute(text("UPDATE models SET approval_status='pending' WHERE enabled"))
