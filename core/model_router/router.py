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
    is_paid: bool = False
    input_per_million_usd: Decimal = Decimal(0)
    output_per_million_usd: Decimal = Decimal(0)


def rejection_reason(
    model: RegisteredModel,
    data_class: DataClass,
    required_capabilities: Sequence[str] = (),
    allow_paid: bool = False,
) -> str | None:
    if model.approval_status is not ApprovalStatus.APPROVED:
        return "not approved"
    if not model.enabled:
        return "disabled"
    if model.pricing is None:
        return "pricing unknown"
    if not model.pricing.is_free and not allow_paid:
        return "paid models are not permitted by governance (disabled, frozen or zero budget)"
    if model.policy.verified_at is None:
        return "provider policy not verified"
    if model.policy.max_data_class < data_class:
        return f"provider approved only up to {model.policy.max_data_class.name.lower()} data"
    have = {c.capability for c in model.capabilities}
    missing = [c for c in required_capabilities if c not in have]
    if missing:
        return "missing capabilities: " + ",".join(missing)
    return None


def _resolved(model: RegisteredModel) -> ResolvedModel:
    pricing = model.pricing
    return ResolvedModel(
        name=model.name,
        provider=model.policy.provider,
        provider_model_id=model.provider_model_id,
        gateway_model_name=model.gateway_model_name,
        max_output_tokens=model.max_output_tokens,
        is_paid=pricing is not None and not pricing.is_free,
        input_per_million_usd=Decimal(pricing.input_per_million) if pricing else Decimal(0),
        output_per_million_usd=Decimal(pricing.output_per_million) if pricing else Decimal(0),
    )


class ModelRouter:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def candidates(
        self,
        alias: str,
        data_class: DataClass,
        required_capabilities: Sequence[str] = (),
        allow_paid: bool = False,
    ) -> list[ResolvedModel]:
        """Eligible models for an alias, in route priority order.

        Rule: a PAID model is a candidate only if NO free route precedes it in priority - whether
        that free route is healthy, disabled or ineligible. So a free model failing or being
        unavailable never silently moves traffic to a paid one; paid -> free fallback is allowed.
        """
        routes = (
            await self._session.execute(
                select(ModelRoute)
                .where(ModelRoute.alias == alias, ModelRoute.enabled.is_(True))
                .order_by(ModelRoute.priority)
            )
        ).scalars()
        found: list[ResolvedModel] = []
        reasons: list[str] = []
        free_route_seen = False
        for route in routes:
            model = await self._session.get(RegisteredModel, route.model_id, populate_existing=True)
            if model is None:  # route row without model cannot happen (FK), but fail closed
                reasons.append(f"{route.model_id}: missing")
                continue
            resolved = _resolved(model)
            reason = rejection_reason(model, data_class, required_capabilities, allow_paid)
            if reason is None and resolved.is_paid and free_route_seen:
                reason = "paid model is never a fallback for an unavailable free model"
            if not resolved.is_paid:
                free_route_seen = True
            if reason is None:
                found.append(resolved)
            else:
                reasons.append(f"{model.name}: {reason}")
        if not found:
            raise NoRouteError(alias, reasons)
        return found

    async def resolve(
        self, alias: str, data_class: DataClass, required_capabilities: Sequence[str] = ()
    ) -> ResolvedModel:
        """First eligible FREE model; paid is excluded here (use candidates())."""
        return (await self.candidates(alias, data_class, required_capabilities, allow_paid=False))[
            0
        ]
