"""End-to-end: agent -> permission -> router -> adapter, using only mock models."""

import os

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_registry import service as agents
from core.model_registry import service as registry
from core.model_router.adapters import LiteLLMAdapter, MockAdapter, ProviderError
from core.model_router.gateway import AgentNotPermittedError, ModelGateway
from core.model_router.router import ModelRouter, NoRouteError
from core.model_router.types import CompletionRequest, Message
from database.schemas import DataClass
from database.seed.registry import FREE_SYNTHETIC_ALIAS, MOCK_ALIAS, seed_registry


def request(dc: DataClass = DataClass.SENSITIVE) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content="synthetic hello")], data_class=dc
    )


def litellm_stub() -> LiteLLMAdapter:
    def handler(r: httpx.Request) -> httpx.Response:
        assert b'"abu-mock"' in r.content  # registry's gateway name, not a caller-supplied id
        return httpx.Response(
            200,
            json={
                "choices": [{"message": {"content": "canned"}}],
                "usage": {"prompt_tokens": 1, "completion_tokens": 1},
            },
        )

    return LiteLLMAdapter(
        "http://litellm:4000", SecretStr("k"), transport=httpx.MockTransport(handler)
    )


@pytest.fixture
async def gateway(session: AsyncSession) -> ModelGateway:
    await seed_registry(session)
    await agents.set_agent_enabled(session, "finance_agent", True)
    await agents.grant_model_alias(session, "finance_agent", MOCK_ALIAS)
    await agents.grant_model_alias(session, "finance_agent", FREE_SYNTHETIC_ALIAS)
    return ModelGateway(session, {"mock": MockAdapter()}, default_adapter=litellm_stub())


async def test_mock_flow_end_to_end(gateway: ModelGateway) -> None:
    res = await gateway.complete(agent_id="finance_agent", alias=MOCK_ALIAS, request=request())
    assert res.model_name == "mock-local" and res.provider == "mock"
    assert res.output_tokens > 0


async def test_fallback_stays_inside_registry(session: AsyncSession, gateway: ModelGateway) -> None:
    await registry.set_enabled(session, "mock-local", False)
    res = await gateway.complete(agent_id="finance_agent", alias=MOCK_ALIAS, request=request())
    assert res.model_name == "litellm-mock" and res.text == "canned"


async def test_all_models_down_is_controlled_error(
    session: AsyncSession, gateway: ModelGateway
) -> None:
    await registry.set_enabled(session, "mock-local", False)
    await registry.set_enabled(session, "litellm-mock", False)
    with pytest.raises(NoRouteError) as exc:
        await gateway.complete(agent_id="finance_agent", alias=MOCK_ALIAS, request=request())
    assert len(exc.value.reasons) == 2


async def test_disabled_agent_refused(session: AsyncSession, gateway: ModelGateway) -> None:
    with pytest.raises(AgentNotPermittedError):
        await gateway.complete(agent_id="messaging_agent", alias=MOCK_ALIAS, request=request())


async def test_alias_not_granted_refused(session: AsyncSession, gateway: ModelGateway) -> None:
    await agents.set_agent_enabled(session, "messaging_agent", True)
    with pytest.raises(AgentNotPermittedError):
        await gateway.complete(agent_id="messaging_agent", alias=MOCK_ALIAS, request=request())


async def test_unknown_alias_cannot_name_a_provider_model(gateway: ModelGateway) -> None:
    await agents.grant_model_alias(gateway._session, "finance_agent", "gemini-3.1-flash-lite")  # noqa: SLF001
    with pytest.raises(NoRouteError):  # a raw provider id is not an alias
        await gateway.complete(
            agent_id="finance_agent", alias="gemini-3.1-flash-lite", request=request()
        )


async def test_free_gemini_route_unavailable_while_disabled(gateway: ModelGateway) -> None:
    with pytest.raises(NoRouteError, match="disabled"):
        await gateway.complete(
            agent_id="finance_agent",
            alias=FREE_SYNTHETIC_ALIAS,
            request=request(DataClass.SYNTHETIC),
        )


async def test_gemini_never_receives_non_synthetic_even_if_enabled(session: AsyncSession) -> None:
    await seed_registry(session)
    # Bypass the service (simulating a bad manual DB edit): enable the model directly.
    await session.execute(
        text("UPDATE models SET enabled = true WHERE name = 'gemini-3.1-flash-lite-free'")
    )
    router = ModelRouter(session)
    ok = await router.resolve(FREE_SYNTHETIC_ALIAS, DataClass.SYNTHETIC)
    assert ok.provider_model_id == "gemini-3.1-flash-lite"
    for dc in (DataClass.NON_SENSITIVE, DataClass.SENSITIVE):
        with pytest.raises(NoRouteError, match="up to synthetic"):
            await router.resolve(FREE_SYNTHETIC_ALIAS, dc)


async def test_paid_model_never_routed_even_if_forced_enabled(session: AsyncSession) -> None:
    await seed_registry(session)
    await session.execute(
        text(
            "UPDATE model_pricing SET input_per_million = 1 WHERE model_id = "
            "(SELECT id FROM models WHERE name='mock-local')"
        )
    )
    res = await ModelRouter(session).resolve(MOCK_ALIAS, DataClass.SYNTHETIC)
    assert res.name == "litellm-mock"  # skipped the (now) paid model, no silent paid use


async def test_missing_adapter_is_controlled_error(session: AsyncSession) -> None:
    await seed_registry(session)
    await agents.set_agent_enabled(session, "finance_agent", True)
    await agents.grant_model_alias(session, "finance_agent", MOCK_ALIAS)
    with pytest.raises(ProviderError):
        await ModelGateway(session, {}).complete(
            agent_id="finance_agent", alias=MOCK_ALIAS, request=request()
        )


LITELLM_URL = os.environ.get("ABU_TEST_LITELLM_URL")
LITELLM_KEY = os.environ.get("ABU_TEST_LITELLM_KEY")


@pytest.mark.skipif(not (LITELLM_URL and LITELLM_KEY), reason="live LiteLLM gateway not configured")
async def test_real_litellm_gateway_mock_model(session: AsyncSession) -> None:
    """Through the REAL LiteLLM container, using its mock_response model (no provider contacted)."""
    assert LITELLM_URL and LITELLM_KEY
    await seed_registry(session)
    await registry.set_enabled(session, "mock-local", False)
    await agents.set_agent_enabled(session, "finance_agent", True)
    await agents.grant_model_alias(session, "finance_agent", MOCK_ALIAS)
    gw = ModelGateway(
        session, {}, default_adapter=LiteLLMAdapter(LITELLM_URL, SecretStr(LITELLM_KEY))
    )
    res = await gw.complete(agent_id="finance_agent", alias=MOCK_ALIAS, request=request())
    assert res.model_name == "litellm-mock"
    assert "canned response from the LiteLLM gateway" in res.text
