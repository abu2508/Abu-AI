"""Model Registry operations. All state changes that matter are validated here.

Policy (Milestone 2):
  * A model is created pending + disabled. It must be explicitly approved, then enabled.
  * Approval needs: a verified provider policy, known pricing, a verified availability date.
  * Paid models can be approved but can NOT be enabled until spending controls exist (Milestone 4).
  * A provider's approved data class starts at SYNTHETIC. Raising it needs an explicit approver,
    a verified policy, and - for SENSITIVE - documented no-training and no-human-review terms.
"""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from core.model_registry.errors import ApprovalError, DuplicateError, NotFoundError
from core.model_registry.schemas import ModelIn, PolicyIn
from database.schemas import (
    ApprovalStatus,
    DataClass,
    ModelCapability,
    ModelPricing,
    ModelRoute,
    ProviderPolicy,
    RegisteredModel,
    SpendingConfig,
    Tri,
)


async def register_policy(session: AsyncSession, data: PolicyIn) -> ProviderPolicy:
    if await find_policy(session, data.provider):
        raise DuplicateError(f"policy for provider {data.provider!r} already exists")
    policy = ProviderPolicy(**data.model_dump(), max_data_class=DataClass.SYNTHETIC)
    session.add(policy)
    await session.flush()
    return policy


async def find_policy(session: AsyncSession, provider: str) -> ProviderPolicy | None:
    return (
        await session.execute(select(ProviderPolicy).where(ProviderPolicy.provider == provider))
    ).scalar_one_or_none()


async def get_policy(session: AsyncSession, provider: str) -> ProviderPolicy:
    policy = await find_policy(session, provider)
    if policy is None:
        raise NotFoundError(f"no policy for provider {provider!r}")
    return policy


async def approve_data_class(
    session: AsyncSession, provider: str, data_class: DataClass, approved_by: str
) -> ProviderPolicy:
    """Raise (or lower) the highest data class a provider may receive."""
    policy = await get_policy(session, provider)
    if data_class > DataClass.SYNTHETIC:
        if not approved_by.strip():
            raise ApprovalError("raising the data class requires a named approver")
        if policy.verified_at is None or not policy.source_url:
            raise ApprovalError("provider policy must be verified (with a source URL) first")
        if policy.trains_on_data is Tri.YES or policy.human_review is Tri.YES:
            raise ApprovalError(
                "provider trains on data or uses human review; only SYNTHETIC data is allowed"
            )
    if data_class is DataClass.SENSITIVE and (
        policy.trains_on_data is not Tri.NO or policy.human_review is not Tri.NO
    ):
        raise ApprovalError(
            "SENSITIVE requires documented no-training and no-human-review terms (unknown is not enough)"
        )
    policy.max_data_class = data_class
    policy.approved_by = approved_by if data_class > DataClass.SYNTHETIC else None
    await session.flush()
    return policy


async def register_model(session: AsyncSession, data: ModelIn) -> RegisteredModel:
    if await find_model(session, data.name):
        raise DuplicateError(f"model {data.name!r} already exists")
    policy = await get_policy(session, data.provider)
    model = RegisteredModel(
        name=data.name,
        policy_id=policy.id,
        provider_model_id=data.provider_model_id,
        gateway_model_name=data.gateway_model_name,
        version=data.version,
        context_limit=data.context_limit,
        max_output_tokens=data.max_output_tokens,
        rpm_limit=data.rpm_limit,
        rpd_limit=data.rpd_limit,
        rate_limits_verified=data.rate_limits_verified,
        availability_verified_at=data.availability_verified_at,
        approval_status=ApprovalStatus.PENDING,
        enabled=False,
        notes=data.notes,
        capabilities=[ModelCapability(capability=c.value) for c in data.capabilities],
        pricing=ModelPricing(**data.pricing.model_dump()),
    )
    session.add(model)
    await session.flush()
    await session.refresh(model, ["policy"])
    return model


async def find_model(session: AsyncSession, name: str) -> RegisteredModel | None:
    return (
        await session.execute(
            select(RegisteredModel)
            .where(RegisteredModel.name == name)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()


async def get_model(session: AsyncSession, name: str) -> RegisteredModel:
    model = await find_model(session, name)
    if model is None:
        raise NotFoundError(f"no model named {name!r}")
    return model


def approval_blockers(model: RegisteredModel) -> list[str]:
    """Everything preventing approval. Empty list = approvable."""
    problems: list[str] = []
    if not model.provider_model_id.strip():
        problems.append("exact provider model id is missing")
    if model.policy.verified_at is None or not model.policy.source_url:
        problems.append("provider policy is not verified")
    if model.pricing is None:
        problems.append("pricing is unknown")
    elif model.pricing.verified_at is None:
        problems.append("pricing is not verified")
    if model.availability_verified_at is None:
        problems.append("availability has not been verified")
    return problems


async def approve_model(session: AsyncSession, name: str) -> RegisteredModel:
    model = await get_model(session, name)
    blockers = approval_blockers(model)
    if blockers:
        raise ApprovalError(f"cannot approve {name!r}: " + "; ".join(blockers))
    model.approval_status = ApprovalStatus.APPROVED
    await session.flush()
    return model


async def reject_model(session: AsyncSession, name: str) -> RegisteredModel:
    model = await get_model(session, name)
    model.approval_status = ApprovalStatus.REJECTED
    model.enabled = False
    await session.flush()
    return model


def is_free(model: RegisteredModel) -> bool:
    return model.pricing is not None and model.pricing.is_free


async def set_enabled(session: AsyncSession, name: str, enabled: bool) -> RegisteredModel:
    model = await get_model(session, name)
    if enabled:
        if model.approval_status is not ApprovalStatus.APPROVED:
            raise ApprovalError(f"cannot enable {name!r}: model is not approved")
        if not is_free(model):
            cfg = await session.get(SpendingConfig, 1)
            if cfg is None or not cfg.paid_models_enabled or cfg.monthly_budget_inr <= 0:
                raise ApprovalError(
                    f"cannot enable {name!r}: it is a paid model and paid models are not opted in "
                    "(an operator must set a budget and enable paid models first)"
                )
    model.enabled = enabled
    await session.flush()
    return model


async def add_route(
    session: AsyncSession, alias: str, model_name: str, priority: int
) -> ModelRoute:
    model = await get_model(session, model_name)
    route = ModelRoute(alias=alias, model_id=model.id, priority=priority, enabled=True)
    session.add(route)
    await session.flush()
    return route


def utcnow() -> datetime:
    return datetime.now(UTC)
