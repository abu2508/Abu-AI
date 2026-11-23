"""End-to-end: RunContext (agent version) -> alias permission -> router -> adapter, mock models only."""

import os
from decimal import Decimal

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_registry import service as agents
from core.governance.service import Governance
from core.model_registry import service as registry
from core.model_router.adapters import LiteLLMAdapter, MockAdapter, ProviderError
from core.model_router.gateway import ModelGateway
from core.model_router.router import ModelRouter, NoRouteError, ResolvedModel
from core.model_router.types import CompletionRequest, CompletionResult, Message
from core.permissions.errors import LimitExceededError, PermissionDeniedError
from core.permissions.runtime import RunContext
from database.schemas import DataClass
from database.seed.registry import FREE_SYNTHETIC_ALIAS, MOCK_ALIAS, seed_registry
from tests.integration.helpers import runnable_agent


def request(
    dc: DataClass = DataClass.SENSITIVE, text_: str = "synthetic hello", max_tokens: int = 256
) -> CompletionRequest:
    return CompletionRequest(
        messages=[Message(role="user", content=text_)], data_class=dc, max_tokens=max_tokens
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


class Spy:
    """Records what the gateway actually sends to the provider."""

    def __init__(self) -> None:
        self.seen: list[CompletionRequest] = []

    async def complete(self, model: ResolvedModel, req: CompletionRequest) -> CompletionResult:
        self.seen.append(req)
        return CompletionResult(
            text="ok",
            input_tokens=5,
            output_tokens=7,
            model_name=model.name,
            provider=model.provider,
            latency_ms=0,
        )


@pytest.fixture
async def ctx(session: AsyncSession, gov: Governance) -> RunContext:
    return await runnable_agent(session, gov, "finance_agent", (MOCK_ALIAS, FREE_SYNTHETIC_ALIAS))


def gateway(session: AsyncSession, gov: Governance) -> ModelGateway:
    return ModelGateway(session, gov, {"mock": MockAdapter()}, default_adapter=litellm_stub())


async def test_mock_flow_end_to_end(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    res = await gateway(session, gov).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    assert res.model_name == "mock-local" and res.provider == "mock" and res.output_tokens > 0
    assert (
        ctx.steps_used == 1 and ctx.output_tokens_used == res.output_tokens
    )  # usage is tracked per run


async def test_fallback_stays_inside_registry(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    await registry.set_enabled(session, "mock-local", False)
    res = await gateway(session, gov).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    assert res.model_name == "litellm-mock" and res.text == "canned"


async def test_all_models_down_is_controlled_error(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    await registry.set_enabled(session, "mock-local", False)
    await registry.set_enabled(session, "litellm-mock", False)
    with pytest.raises(NoRouteError) as exc:
        await gateway(session, gov).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    assert len(exc.value.reasons) == 2


async def test_alias_not_granted_to_this_version_is_refused(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(
        session, gov, "messaging_agent", ()
    )  # active, enabled, but no model grants
    with pytest.raises(PermissionDeniedError, match="may not use"):
        await gateway(session, gov).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())


async def test_raw_provider_model_id_cannot_be_used_as_an_alias(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    with pytest.raises(PermissionDeniedError):
        await gateway(session, gov).complete(
            ctx=ctx, alias="gemini-3.1-flash-lite", request=request()
        )


async def test_free_gemini_route_unavailable_while_disabled(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    with pytest.raises(NoRouteError, match="disabled"):
        await gateway(session, gov).complete(
            ctx=ctx, alias=FREE_SYNTHETIC_ALIAS, request=request(DataClass.SYNTHETIC)
        )


async def test_gemini_never_receives_non_synthetic_even_if_enabled(session: AsyncSession) -> None:
    await seed_registry(session)
    await session.execute(
        text("UPDATE models SET enabled = true WHERE name = 'gemini-3.1-flash-lite-free'")
    )  # bad manual edit
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
            "UPDATE model_pricing SET input_per_million = 1 WHERE model_id = (SELECT id FROM models WHERE name='mock-local')"
        )
    )
    res = await ModelRouter(session).resolve(MOCK_ALIAS, DataClass.SYNTHETIC)
    assert res.name == "litellm-mock"  # skipped the (now) paid model, no silent paid use


async def test_missing_adapter_is_controlled_error(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    with pytest.raises(ProviderError):
        await ModelGateway(session, gov, {}).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())


# --- per-run limits enforced by the gateway -------------------------------------------------------------


async def test_output_tokens_clamped_to_remaining_run_budget(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    spy = Spy()
    gw = ModelGateway(session, gov, {"mock": spy})
    await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=request(max_tokens=8192))
    assert spy.seen[0].max_tokens == ctx.limits.max_output_tokens  # asked for 8192, allowed 4000
    ctx.output_tokens_used = ctx.limits.max_output_tokens - 3
    await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=request(max_tokens=100))
    assert spy.seen[1].max_tokens == 3


async def test_run_stops_when_output_budget_exhausted(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    ctx.output_tokens_used = ctx.limits.max_output_tokens
    with pytest.raises(LimitExceededError, match="output token"):
        await gateway(session, gov).complete(ctx=ctx, alias=MOCK_ALIAS, request=request())


async def test_input_token_budget_enforced_before_any_provider_call(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    spy = Spy()
    with pytest.raises(LimitExceededError, match="input token"):
        await ModelGateway(session, gov, {"mock": spy}).complete(
            ctx=ctx,
            alias=MOCK_ALIAS,
            request=request(text_="x" * (ctx.limits.max_input_tokens * 4 + 100)),
        )
    assert spy.seen == []


async def test_step_limit_enforced_across_calls(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    gw = gateway(session, gov)
    for _ in range(ctx.limits.max_steps):
        await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=request(max_tokens=1))
        ctx.output_tokens_used = 0  # isolate the step limit from the token budget
    with pytest.raises(LimitExceededError, match="steps"):
        await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=request(max_tokens=1))


async def test_slow_provider_hits_the_run_deadline(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    import asyncio

    class Slow:
        async def complete(self, model: ResolvedModel, req: CompletionRequest) -> CompletionResult:
            await asyncio.sleep(5)
            raise AssertionError("unreachable")

    ctx.limits = type(ctx.limits)(8, 1, 20_000, 4_000, Decimal(0))
    with pytest.raises(LimitExceededError, match="timeout"):
        await ModelGateway(session, gov, {"mock": Slow()}).complete(
            ctx=ctx, alias=MOCK_ALIAS, request=request()
        )


async def test_disabling_the_agent_stops_new_runs_but_not_the_audit_of_history(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    from core.model_registry.errors import ApprovalError  # noqa: F401
    from core.permissions.errors import AgentNotPermittedError
    from core.permissions.runtime import start_run

    await agents.set_agent_enabled(session, "finance_agent", False)
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)


LITELLM_URL = os.environ.get("ABU_TEST_LITELLM_URL")
LITELLM_KEY = os.environ.get("ABU_TEST_LITELLM_KEY")


@pytest.mark.skipif(not (LITELLM_URL and LITELLM_KEY), reason="live LiteLLM gateway not configured")
async def test_real_litellm_gateway_mock_model(
    session: AsyncSession, ctx: RunContext, gov: Governance
) -> None:
    """Through the REAL LiteLLM container, using its mock_response model (no provider contacted)."""
    assert LITELLM_URL and LITELLM_KEY
    await registry.set_enabled(session, "mock-local", False)
    gw = ModelGateway(
        session, gov, {}, default_adapter=LiteLLMAdapter(LITELLM_URL, SecretStr(LITELLM_KEY))
    )
    res = await gw.complete(ctx=ctx, alias=MOCK_ALIAS, request=request())
    assert res.model_name == "litellm-mock"
    assert "canned response from the LiteLLM gateway" in res.text
