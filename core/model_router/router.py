"""Backend routing policy. Only registered, approved, enabled, free, policy-compatible models
are ever returned. Callers ask for a logical alias; nobody (including an LLM) names a provider
model id."""

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database.schemas import ApprovalStatus, DataClass, ModelRoute, RegisteredModel


class NoRouteError(Exception):
    """No eligible model. Carries per-candidate reasons (no request content)."""

    def __init__(self, alias: str, reasons: list[str]) -> None:
        self.alias = alias
        self.reasons = reasons
        super().__init__(
            f"no eligible model for alias {alias!r}: " + "; ".join(reasons or ["no routes"])
        )


@dataclass(frozen=True)
class ResolvedModel:
    name: str
    provider: str
    provider_model_id: str
    gateway_model_name: str
    max_output_tokens: int | None
    input_per_million_usd: Decimal = Decimal(0)
    output_per_million_usd: Decimal = Decimal(0)


def rejection_reason(
    model: RegisteredModel, data_class: DataClass, required_capabilities: Sequence[str] = ()
) -> str | None:
    if model.approval_status is not ApprovalStatus.APPROVED:
        return "not approved"
    if not model.enabled:
        return "disabled"
    if model.pricing is None:
        return "pricing unknown"
    if not model.pricing.is_free:
        return "paid models are not routable (spending controls arrive in Milestone 4)"
    if model.policy.verified_at is None:
        return "provider policy not verified"
    if model.policy.max_data_class < data_class:
        return f"provider approved only up to {model.policy.max_data_class.name.lower()} data"
    have = {c.capability for c in model.capabilities}
    missing = [c for c in required_capabilities if c not in have]
    if missing:
        return "missing capabilities: " + ",".join(missing)
    return None


class ModelRouter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def resolve(
        self, alias: str, data_class: DataClass, required_capabilities: Sequence[str] = ()
    ) -> ResolvedModel:
        routes = (
            await self._session.execute(
                select(ModelRoute)
                .where(ModelRoute.alias == alias, ModelRoute.enabled.is_(True))
                .order_by(ModelRoute.priority)
            )
        ).scalars()
        reasons: list[str] = []
        for route in routes:
            model = await self._session.get(RegisteredModel, route.model_id, populate_existing=True)
            if model is None:  # route row without model cannot happen (FK), but fail closed
                reasons.append(f"{route.model_id}: missing")
                continue
            reason = rejection_reason(model, data_class, required_capabilities)
            if reason is None:
                return ResolvedModel(
                    name=model.name,
                    provider=model.policy.provider,
                    provider_model_id=model.provider_model_id,
                    gateway_model_name=model.gateway_model_name,
                    max_output_tokens=model.max_output_tokens,
                    input_per_million_usd=Decimal(model.pricing.input_per_million)
                    if model.pricing
                    else Decimal(0),
                    output_per_million_usd=Decimal(model.pricing.output_per_million)
                    if model.pricing
                    else Decimal(0),
                )
            reasons.append(f"{model.name}: {reason}")
        raise NoRouteError(alias, reasons)
