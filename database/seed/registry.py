"""Idempotent seed data. Goes through the validating registry services, so seed data obeys the
same rules as operator actions. Existing rows are never overwritten.

Provider facts below were read from Google's own pages on 2026-11-23:
  pricing : https://ai.google.dev/gemini-api/docs/pricing   (free tier $0; "used to improve products: Yes")
  model   : https://ai.google.dev/gemini-api/docs/models/gemini-3.1-flash-lite
  terms   : https://ai.google.dev/gemini-api/terms          (unpaid quota: product improvement + human review)
Per-model rate limits are not published in the docs (per-project in AI Studio) => left unverified.
The Gemini model is registered approved-for-synthetic-data-only and DISABLED: no API key is
configured and no live call has been made.
"""

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from core.agent_registry import service as agents
from core.model_registry import service as registry
from core.model_registry.schemas import Capability, ModelIn, PolicyIn, PricingIn
from database.schemas import Agent, DataClass, Tri

VERIFIED = datetime(2026, 11, 23, tzinfo=UTC)

MOCK_ALIAS = "test-mock"
FREE_SYNTHETIC_ALIAS = "general-free-synthetic"

AGENTS = [
    ("finance_agent", "Finance Agent", "Bank/card/UPI tracking, budgets and financial Q&A."),
    ("messaging_agent", "Messaging Agent", "Per-person conversation context and reply drafts."),
    ("daily_coach_agent", "Daily Coach", "Planning, tasks, habits, goals and reviews."),
]

_FREE = PricingIn(
    input_per_million=0, output_per_million=0, source_url="internal:local", verified_at=VERIFIED
)


async def _ensure_policy(session: AsyncSession, data: PolicyIn) -> None:
    if await registry.find_policy(session, data.provider) is None:
        await registry.register_policy(session, data)


async def _ensure_model(session: AsyncSession, data: ModelIn, *, enable: bool) -> None:
    if await registry.find_model(session, data.name) is not None:
        return
    await registry.register_model(session, data)
    await registry.approve_model(session, data.name)
    if enable:
        await registry.set_enabled(session, data.name, True)


async def seed_registry(session: AsyncSession) -> None:
    # --- local mock providers: nothing leaves our own infrastructure ---------------------
    for provider, summary in (
        ("mock", "In-process mock. No data leaves the process; nothing is retained."),
        (
            "litellm-mock",
            "LiteLLM mock_response. Request stays on the private Docker network; no provider is contacted.",
        ),
    ):
        if await registry.find_policy(session, provider) is None:
            await registry.register_policy(
                session,
                PolicyIn(
                    provider=provider,
                    retention_summary=summary,
                    trains_on_data=Tri.NO,
                    human_review=Tri.NO,
                    source_url="internal:local",
                    verified_at=VERIFIED,
                ),
            )
            await registry.approve_data_class(
                session, provider, DataClass.SENSITIVE, "system:local-mock"
            )

    caps = [Capability.TEXT_GENERATION]
    await _ensure_model(
        session,
        ModelIn(
            name="mock-local",
            provider="mock",
            provider_model_id="mock-local",
            gateway_model_name="mock-local",
            capabilities=caps,
            pricing=_FREE,
            availability_verified_at=VERIFIED,
            notes="In-process adapter for hermetic tests.",
        ),
        enable=True,
    )
    await _ensure_model(
        session,
        ModelIn(
            name="litellm-mock",
            provider="litellm-mock",
            provider_model_id="abu-mock",
            gateway_model_name="abu-mock",
            capabilities=caps,
            pricing=_FREE,
            availability_verified_at=VERIFIED,
            notes="LiteLLM model with a canned mock_response; exercises the gateway path.",
        ),
        enable=True,
    )

    # --- first genuinely free model: synthetic data only, disabled until a key is supplied ---
    await _ensure_policy(
        session,
        PolicyIn(
            provider="google-ai-studio-free",
            retention_summary=(
                "Unpaid Gemini API quota: submitted content and responses are used to improve "
                "Google products; human reviewers may read them. Do not submit sensitive, "
                "confidential or personal information."
            ),
            trains_on_data=Tri.YES,
            human_review=Tri.YES,
            source_url="https://ai.google.dev/gemini-api/terms",
            verified_at=VERIFIED,
        ),
    )
    await _ensure_model(
        session,
        ModelIn(
            name="gemini-3.1-flash-lite-free",
            provider="google-ai-studio-free",
            provider_model_id="gemini-3.1-flash-lite",
            gateway_model_name="gemini-3.1-flash-lite",
            context_limit=1_048_576,
            max_output_tokens=65_536,
            rate_limits_verified=False,
            availability_verified_at=VERIFIED,
            capabilities=[
                Capability.TEXT_GENERATION,
                Capability.STRUCTURED_OUTPUT,
                Capability.FUNCTION_CALLING,
                Capability.VISION,
                Capability.LONG_CONTEXT,
            ],
            pricing=PricingIn(
                input_per_million=0,
                output_per_million=0,
                source_url="https://ai.google.dev/gemini-api/docs/pricing",
                verified_at=VERIFIED,
            ),
            notes=(
                "Free tier. Documented availability only; no live call made. RPM/RPD not published "
                "in docs. Synthetic data only (provider trains on data)."
            ),
        ),
        enable=False,
    )

    # --- routes (idempotent) -------------------------------------------------------------
    from sqlalchemy import select

    from database.schemas import ModelRoute

    existing = set((await session.execute(select(ModelRoute.alias, ModelRoute.priority))).all())
    for alias, model_name, priority in (
        (MOCK_ALIAS, "mock-local", 1),
        (MOCK_ALIAS, "litellm-mock", 2),
        (FREE_SYNTHETIC_ALIAS, "gemini-3.1-flash-lite-free", 1),
    ):
        if (alias, priority) not in existing:
            await registry.add_route(session, alias, model_name, priority)

    # --- the three mandatory agents: registered, disabled, no permissions ----------------
    for agent_id, name, description in AGENTS:
        if await session.get(Agent, agent_id) is None:
            await agents.register_agent(session, agent_id, name, description)
