"""ModelGateway: the single path from an agent run to a model.

RunContext (built server-side from the active, enabled agent version)
  -> step/deadline check -> alias granted? -> input-token budget
  -> router picks an eligible registered model (never a caller-supplied id)
  -> output tokens clamped to the remaining budget; worst-case cost checked
  -> adapter call under the remaining deadline -> usage recorded.
Any failure is a controlled exception; there is no fallback to a model outside the registry and
no silent move to a paid model (paid models are not routable until Milestone 4).
"""

import asyncio
import logging
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.model_router.adapters import ProviderAdapter, ProviderError
from core.model_router.router import ModelRouter
from core.model_router.types import CompletionRequest, CompletionResult
from core.permissions.errors import AgentNotPermittedError, LimitExceededError
from core.permissions.runtime import RunContext, estimate_cost_inr

logger = logging.getLogger(__name__)

__all__ = ["AgentNotPermittedError", "ModelGateway"]


def estimate_input_tokens(request: CompletionRequest) -> int:
    return sum(len(m.content) for m in request.messages) // 4 + 1


class ModelGateway:
    def __init__(
        self,
        session: AsyncSession,
        adapters: dict[str, ProviderAdapter],
        default_adapter: ProviderAdapter | None = None,
        usd_inr_rate: Decimal = Decimal("100"),
    ) -> None:
        self._session = session
        self._router = ModelRouter(session)
        self._adapters = adapters
        self._default = default_adapter
        self._rate = usd_inr_rate

    async def complete(
        self, *, ctx: RunContext, alias: str, request: CompletionRequest
    ) -> CompletionResult:
        ctx.begin_step()
        ctx.authorize_alias(alias)

        est_in = estimate_input_tokens(request)
        if est_in > ctx.remaining_input_tokens:
            raise LimitExceededError("per-run input token limit would be exceeded")
        if ctx.remaining_output_tokens <= 0:
            raise LimitExceededError("per-run output token limit reached")

        model = await self._router.resolve(alias, request.data_class)
        max_tokens = min(request.max_tokens, ctx.remaining_output_tokens)
        if model.max_output_tokens:
            max_tokens = min(max_tokens, model.max_output_tokens)
        worst_case = estimate_cost_inr(
            est_in,
            max_tokens,
            model.input_per_million_usd,
            model.output_per_million_usd,
            self._rate,
        )
        if worst_case > ctx.remaining_cost_inr:
            raise LimitExceededError("per-run cost limit would be exceeded")

        adapter = self._adapters.get(model.provider, self._default)
        if adapter is None:
            raise ProviderError(f"no adapter configured for provider {model.provider!r}")

        try:
            async with asyncio.timeout(ctx.remaining_seconds()):
                result = await adapter.complete(
                    model, request.model_copy(update={"max_tokens": max_tokens})
                )
        except TimeoutError:
            raise LimitExceededError("run timeout exceeded") from None

        cost = estimate_cost_inr(
            result.input_tokens,
            result.output_tokens,
            model.input_per_million_usd,
            model.output_per_million_usd,
            self._rate,
        )
        ctx.record_usage(result.input_tokens, result.output_tokens, cost)
        # Metadata only: prompts and completions are never logged.
        logger.info(
            "model_call agent=%s v=%d alias=%s model=%s provider=%s in=%d out=%d ms=%.1f",
            ctx.agent_id,
            ctx.version,
            alias,
            result.model_name,
            result.provider,
            result.input_tokens,
            result.output_tokens,
            result.latency_ms,
        )
        return result
