"""ModelGateway: the single path from an agent to a model.

agent enabled? -> agent allowed this alias? -> router picks an eligible registered model
-> adapter transports the call. Any failure is a controlled exception; there is no
fallback to a model outside the registry and no silent move to a paid model.
"""

import logging

from sqlalchemy.ext.asyncio import AsyncSession

from core.model_router.adapters import ProviderAdapter, ProviderError
from core.model_router.router import ModelRouter
from core.model_router.types import CompletionRequest, CompletionResult
from database.schemas import Agent, AgentModelPermission

logger = logging.getLogger(__name__)


class AgentNotPermittedError(Exception):
    pass


class ModelGateway:
    def __init__(
        self,
        session: AsyncSession,
        adapters: dict[str, ProviderAdapter],
        default_adapter: ProviderAdapter | None = None,
    ) -> None:
        self._session = session
        self._router = ModelRouter(session)
        self._adapters = adapters
        self._default = default_adapter

    async def complete(
        self, *, agent_id: str, alias: str, request: CompletionRequest
    ) -> CompletionResult:
        agent = await self._session.get(Agent, agent_id, populate_existing=True)
        if agent is None or not agent.enabled:
            raise AgentNotPermittedError(f"agent {agent_id!r} is unknown or disabled")
        permission = await self._session.get(AgentModelPermission, (agent_id, alias))
        if permission is None:
            raise AgentNotPermittedError(f"agent {agent_id!r} may not use alias {alias!r}")

        model = await self._router.resolve(alias, request.data_class)
        adapter = self._adapters.get(model.provider, self._default)
        if adapter is None:
            raise ProviderError(f"no adapter configured for provider {model.provider!r}")

        result = await adapter.complete(model, request)
        # Metadata only: prompts and completions are never logged.
        logger.info(
            "model_call agent=%s alias=%s model=%s provider=%s in=%d out=%d ms=%.1f",
            agent_id,
            alias,
            result.model_name,
            result.provider,
            result.input_tokens,
            result.output_tokens,
            result.latency_ms,
        )
        return result
