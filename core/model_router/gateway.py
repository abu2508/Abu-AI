"""ModelGateway: the single path from an agent run to a model.

Per call:
  kill switch (fail closed) -> step/deadline -> alias granted? -> input-token budget
  -> router lists eligible candidates (paid ones only if governance allows AND no free route
     precedes them)
  -> for each candidate in order:
       paid?  RESERVE the worst-case INR in the ledger (own committed transaction, serialised)
       call the adapter under the remaining run deadline
       paid?  SETTLE with the confirmed cost / HOLD if possibly charged / RELEASE if not charged
       write an execution_calls row (metadata only, durable)
  -> first success wins; failures fall through to the next candidate.

Fail-closed rules: unknown cost => hold at worst case + freeze paid spending; a timeout or 5xx
after send => hold + freeze; the provider is never called twice for the same ledger key.
There is no fallback to a model outside the registry and never a silent move to a paid model.
"""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from core.governance.errors import (
    BudgetExceededError,
    DuplicateCallError,
    PaidSpendingBlockedError,
)
from core.governance.service import Governance
from core.model_router.adapters import ProviderAdapter, ProviderError
from core.model_router.router import ModelRouter, NoRouteError, ResolvedModel
from core.model_router.types import CompletionRequest, CompletionResult
from core.permissions.errors import AgentNotPermittedError, LimitExceededError
from core.permissions.runtime import RunContext

logger = logging.getLogger(__name__)

__all__ = ["AgentNotPermittedError", "CostOutcome", "ModelGateway", "determine_cost"]

ZERO = Decimal(0)
MILLION = Decimal(1_000_000)


def estimate_input_tokens(request: CompletionRequest) -> int:
    return sum(len(m.content) for m in request.messages) // 4 + 1


@dataclass(frozen=True)
class CostOutcome:
    usd: Decimal | None  # None = could not be determined (fail closed)
    source: str | None  # gateway_reported | registry_price


def determine_cost(result: CompletionResult, model: ResolvedModel) -> CostOutcome:
    """ONE confirmed figure per call, so LiteLLM and the application never double count.
    Preference: the gateway's own reported cost; else provider-reported usage x registry price.
    Anything else is UNKNOWN - never assumed to be zero."""
    if not model.is_paid:
        return CostOutcome(ZERO, "free")
    if result.provider_cost_usd is not None and result.provider_cost_usd >= 0:
        return CostOutcome(result.provider_cost_usd, "gateway_reported")
    if result.usage_reported and result.input_tokens >= 0 and result.output_tokens >= 0:
        usd = (
            result.input_tokens * model.input_per_million_usd
            + result.output_tokens * model.output_per_million_usd
        ) / MILLION
        return CostOutcome(usd, "registry_price")
    return CostOutcome(None, None)


def _blocked_reason(exc: Exception) -> str:
    if isinstance(exc, BudgetExceededError):
        return f"budget_exceeded_{exc.scope}"
    return "paid_blocked"


class ModelGateway:
    def __init__(
        self,
        session: AsyncSession,
        governance: Governance,
        adapters: dict[str, ProviderAdapter],
        default_adapter: ProviderAdapter | None = None,
    ) -> None:
        self._session = session
        self._gov = governance
        self._router = ModelRouter(session)
        self._adapters = adapters
        self._default = default_adapter

    async def _record(self, ctx: RunContext, **fields: Any) -> None:
        now = self._gov.clock()
        fields.setdefault("started_at", now)
        fields.setdefault("completed_at", now)
        await self._gov.call(execution_id=ctx.execution_id, **fields)

    async def complete(
        self, *, ctx: RunContext, alias: str, request: CompletionRequest
    ) -> CompletionResult:
        gov = self._gov
        policy = await gov.policy()  # kill switch + spending policy; fails closed
        ctx.begin_step()
        ctx.authorize_alias(alias)
        step = ctx.steps_used

        est_in = estimate_input_tokens(request)
        if est_in > ctx.remaining_input_tokens:
            raise LimitExceededError("per-run input token limit would be exceeded")
        if ctx.remaining_output_tokens <= 0:
            raise LimitExceededError("per-run output token limit reached")

        try:
            candidates = await self._router.candidates(
                alias, request.data_class, allow_paid=policy.paid_allowed
            )
        except NoRouteError:
            await self._record(
                ctx,
                step=step,
                attempt=0,
                status="blocked",
                failure_reason="no_route",
                model_alias=alias,
                cost_status="none",
                policy_decisions={
                    "data_class": request.data_class.name.lower(),
                    "paid_block": policy.paid_block_reason or "none",
                },
            )
            raise

        previous: ResolvedModel | None = None
        last_exc: Exception | None = None
        for attempt, model in enumerate(candidates):
            if attempt:
                await gov.require_execution_allowed()  # re-checked before every fallback
            remaining_s = ctx.remaining_seconds()
            max_tokens = min(request.max_tokens, ctx.remaining_output_tokens)
            if model.max_output_tokens:
                max_tokens = min(max_tokens, model.max_output_tokens)
            common: dict[str, Any] = {
                "step": step,
                "attempt": attempt,
                "model_alias": alias,
                "model_name": model.name,
                "provider": model.provider,
                "provider_model_id": model.provider_model_id,
                "fallback_used": previous is not None,
                "fallback_from": previous.name if previous else None,
            }
            decisions: dict[str, Any] = {
                "kill_switch": "inactive",
                "data_class": request.data_class.name.lower(),
                "paid": "yes" if model.is_paid else "no",
            }

            reservation = None
            estimated_inr = ZERO
            if model.is_paid:
                worst_usd = (
                    est_in * model.input_per_million_usd + max_tokens * model.output_per_million_usd
                ) / MILLION
                try:
                    reservation = await gov.spend.reserve(
                        execution_id=ctx.execution_id,
                        user_id=ctx.user_id,
                        agent_id=ctx.agent_id,
                        provider=model.provider,
                        model_name=model.name,
                        call_key=f"{ctx.execution_id}:{step}:{attempt}",
                        worst_case_usd=worst_usd,
                        run_limit_inr=ctx.limits.max_cost_inr,
                    )
                except (BudgetExceededError, PaidSpendingBlockedError) as exc:
                    decisions["budget"] = "refused"
                    await self._record(
                        ctx,
                        status="blocked",
                        failure_reason=_blocked_reason(exc),
                        cost_status="none",
                        policy_decisions=decisions,
                        **common,
                    )
                    last_exc, previous = exc, model
                    continue
                if (
                    reservation.existing
                ):  # same ledger key already used: never call the provider twice
                    await self._record(
                        ctx,
                        status="blocked",
                        failure_reason="duplicate_call",
                        cost_status="none",
                        ledger_id=reservation.id,
                        policy_decisions=decisions,
                        **common,
                    )
                    raise DuplicateCallError("this call was already attempted")
                estimated_inr = reservation.reserved_inr
                decisions["budget"] = "reserved"
            common["estimated_cost_inr"] = estimated_inr
            common["usd_inr_rate"] = reservation.usd_inr_rate if reservation else None
            common["ledger_id"] = reservation.id if reservation else None

            adapter = self._adapters.get(model.provider, self._default)
            if adapter is None:
                if reservation:
                    await gov.spend.release(reservation.id, "no adapter configured")
                await self._record(
                    ctx,
                    status="failed",
                    failure_reason="no_adapter",
                    cost_status="none",
                    policy_decisions=decisions,
                    **common,
                )
                last_exc, previous = (
                    ProviderError(f"no adapter configured for provider {model.provider!r}"),
                    model,
                )
                continue

            started = gov.clock()
            t0 = time.perf_counter()
            try:
                async with asyncio.timeout(remaining_s):
                    result = await adapter.complete(
                        model, request.model_copy(update={"max_tokens": max_tokens})
                    )
            except TimeoutError:
                if reservation:
                    await gov.spend.hold(reservation.id, "provider timeout")
                await self._record(
                    ctx,
                    status="failed",
                    failure_reason="timeout",
                    cost_status="unknown" if reservation else "none",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    started_at=started,
                    policy_decisions=decisions,
                    **common,
                )
                raise LimitExceededError(
                    "run timeout exceeded"
                ) from None  # deadline gone: stop here
            except ProviderError as exc:
                if reservation:
                    if exc.maybe_charged:
                        await gov.spend.hold(reservation.id, "provider error after send")
                    else:
                        await gov.spend.release(reservation.id, "request not accepted by provider")
                await self._record(
                    ctx,
                    status="failed",
                    failure_reason="provider_error_maybe_charged"
                    if exc.maybe_charged
                    else "provider_rejected",
                    cost_status="unknown" if (reservation and exc.maybe_charged) else "none",
                    latency_ms=(time.perf_counter() - t0) * 1000,
                    started_at=started,
                    policy_decisions=decisions,
                    **common,
                )
                last_exc, previous = exc, model
                continue
            except asyncio.CancelledError:
                if (
                    reservation
                ):  # the provider may already be processing: never leave it as "reserved"
                    await asyncio.shield(gov.spend.hold(reservation.id, "cancelled mid-call"))
                raise
            except Exception:
                if reservation:
                    await gov.spend.hold(reservation.id, "unexpected error mid-call")
                raise

            # ---- success: confirm the cost -------------------------------------------------
            cost = determine_cost(result, model)
            confirmed_inr: Decimal | None
            if not model.is_paid:
                confirmed_inr, cost_status = ZERO, "free"
            else:
                if reservation is None:  # unreachable: paid candidates are always reserved first
                    raise RuntimeError("paid call without a ledger reservation")
                settlement = await gov.spend.settle(reservation.id, confirmed_usd=cost.usd)
                if settlement.state == "settled":
                    confirmed_inr, cost_status = settlement.confirmed_inr, "confirmed"
                else:  # unknown cost: held at worst case, paid spending frozen
                    confirmed_inr, cost_status = None, "unknown"
                    decisions["budget"] = "held_unknown_cost"
            ctx.record_usage(
                result.input_tokens,
                result.output_tokens,
                confirmed_inr if confirmed_inr is not None else estimated_inr,
            )
            await self._record(
                ctx,
                status="succeeded",
                input_tokens=result.input_tokens,
                output_tokens=result.output_tokens,
                confirmed_cost_inr=confirmed_inr,
                cost_status=cost_status,
                cost_source=cost.source,
                provider_cost_usd=result.provider_cost_usd,
                latency_ms=result.latency_ms,
                started_at=started,
                completed_at=datetime.now(UTC),
                policy_decisions=decisions,
                **common,
            )
            # Metadata only: prompts and completions are never logged.
            logger.info(
                "model_call trace=%s agent=%s v=%d alias=%s model=%s provider=%s in=%d out=%d ms=%.1f fb=%s",
                ctx.trace_id,
                ctx.agent_id,
                ctx.version,
                alias,
                result.model_name,
                result.provider,
                result.input_tokens,
                result.output_tokens,
                result.latency_ms,
                previous is not None,
            )
            return result

        raise last_exc or ProviderError("no candidate succeeded")
