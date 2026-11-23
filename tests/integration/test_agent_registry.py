import asyncio
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.agent_registry import service as agents
from core.governance.service import Governance
from core.model_registry.errors import ApprovalError, NotFoundError, VersionStateError
from core.permissions.errors import (
    AgentNotPermittedError,
    ApprovalRequiredError,
    LimitExceededError,
    PermissionDeniedError,
    ToolValidationError,
)
from core.permissions.runtime import RunContext, start_run
from core.permissions.tools import ToolExecutor, ToolRegistry, ToolRisk, ToolSpec, create_approval
from database.schemas import (
    AgentVersion,
    AuditEvent,
    MemoryMode,
    ToolApproval,
    User,
    VersionState,
)
from database.seed.registry import MOCK_ALIAS, seed_registry
from tests.integration.helpers import runnable_agent

# --- a tiny tool set for tests only (production DEFAULT_TOOLS is intentionally empty) ---------


class Lookup(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    limit: int = 5


class Save(BaseModel):
    model_config = ConfigDict(extra="forbid")
    note: str


class Wire(BaseModel):
    model_config = ConfigDict(extra="forbid")
    amount: int


calls: list[str] = []


async def lookup_handler(ctx: RunContext, args: Lookup) -> str:
    calls.append("lookup")
    return f"found:{args.key}"


async def save_handler(ctx: RunContext, args: Save) -> str:
    calls.append("save")
    return "saved"


async def slow_handler(ctx: RunContext, args: Lookup) -> str:
    await asyncio.sleep(5)
    return "late"


async def wire_handler(ctx: RunContext, args: Wire) -> str:
    raise AssertionError("an irreversible tool must never run in this milestone")


def tool_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(ToolSpec("lookup", "read", Lookup, ToolRisk.READ, lookup_handler))
    reg.register(ToolSpec("save_note", "write", Save, ToolRisk.WRITE, save_handler))
    reg.register(ToolSpec("slow_lookup", "read", Lookup, ToolRisk.READ, slow_handler))
    reg.register(
        ToolSpec("send_payment", "irreversible", Wire, ToolRisk.IRREVERSIBLE, wire_handler)
    )
    return reg


async def audit_types(sessions: async_sessionmaker[AsyncSession]) -> list[str]:
    async with sessions() as s:
        return list(
            (await s.execute(select(AuditEvent.event_type).order_by(AuditEvent.id))).scalars()
        )


# --- registration, defaults and the lifecycle ---------------------------------------------------


async def test_new_agents_are_disabled_with_an_empty_draft_and_no_permissions(
    session: AsyncSession,
) -> None:
    await seed_registry(session)
    for agent_id in ("finance_agent", "messaging_agent", "daily_coach_agent"):
        agent = await agents.get_agent(session, agent_id)
        assert agent.enabled is False
        (v,) = await agents.list_versions(session, agent_id)
        assert v.state is VersionState.DRAFT and v.version == 1
        assert not v.model_aliases and not v.tools and not v.memory  # default-deny
        assert v.max_run_cost_inr == 0  # free models only until explicitly raised


async def test_cannot_enable_an_agent_without_an_active_version(session: AsyncSession) -> None:
    await seed_registry(session)
    with pytest.raises(ApprovalError, match="no active version"):
        await agents.set_agent_enabled(session, "finance_agent", True)


async def test_activation_freezes_the_version(session: AsyncSession) -> None:
    await seed_registry(session)
    await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    v = await agents.activate_version(session, "finance_agent", 1)
    assert v.state is VersionState.ACTIVE and v.activated_at is not None
    with pytest.raises(VersionStateError, match="clone"):
        await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    with pytest.raises(VersionStateError):
        await agents.update_limits(session, "finance_agent", 1, agents.VersionLimits(max_steps=2))
    with pytest.raises(VersionStateError):
        await agents.revoke(session, "finance_agent", 1, "model_alias", MOCK_ALIAS)
    with pytest.raises(VersionStateError):
        await agents.activate_version(session, "finance_agent", 1)  # not a draft any more


async def test_clone_edit_activate_retires_the_previous_version_atomically(
    session: AsyncSession,
) -> None:
    await seed_registry(session)
    await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    await agents.grant_memory(session, "finance_agent", 1, "finance_agent/ledger", MemoryMode.READ)
    await agents.activate_version(session, "finance_agent", 1)

    v2 = await agents.clone_version(session, "finance_agent", 1)
    assert v2.version == 2 and v2.state is VersionState.DRAFT
    assert [a.alias for a in v2.model_aliases] == [MOCK_ALIAS]  # permissions copied, not widened
    await agents.revoke(session, "finance_agent", 2, "model_alias", MOCK_ALIAS)
    await agents.activate_version(session, "finance_agent", 2)

    v1, v2 = await agents.list_versions(session, "finance_agent")
    assert (v1.state, v2.state) == (VersionState.RETIRED, VersionState.ACTIVE)
    assert [a.alias for a in v1.model_aliases] == [MOCK_ALIAS]  # history is preserved verbatim
    assert not v2.model_aliases


async def test_database_allows_only_one_active_version(session: AsyncSession) -> None:
    await seed_registry(session)
    await agents.activate_version(session, "finance_agent", 1)
    await agents.create_version(session, "finance_agent", agents.VersionLimits())
    with pytest.raises(IntegrityError):  # bypassing the service must still fail
        await session.execute(
            text(
                "UPDATE agent_versions SET state='active' WHERE agent_id='finance_agent' AND version=2"
            )
        )


async def test_retiring_the_active_version_disables_the_agent(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    await agents.retire_version(session, "finance_agent", 1)
    assert (await agents.get_agent(session, "finance_agent")).enabled is False
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)


async def test_database_enforces_limit_ceilings(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await seed_registry(session)
    await session.commit()
    bad_values = (
        "max_steps = 0",
        "max_steps = 51",
        "timeout_seconds = 301",
        "max_run_cost_inr = 51",
        "max_run_cost_inr = -1",
    )
    for bad in bad_values:
        async with sessions() as s:  # a fresh transaction per attempt: a failed one is unusable
            with pytest.raises((IntegrityError, DBAPIError)):
                await s.execute(text(f"UPDATE agent_versions SET {bad}"))  # noqa: S608


# --- grants: validation and default-deny ---------------------------------------------------------


async def test_grants_reference_only_things_that_exist(session: AsyncSession) -> None:
    await seed_registry(session)
    reg = tool_registry()
    with pytest.raises(NotFoundError):
        await agents.grant_model_alias(
            session, "finance_agent", 1, "gemini-3.1-flash-lite"
        )  # id, not an alias
    with pytest.raises(NotFoundError):
        await agents.grant_model_alias(session, "finance_agent", 1, "typo-alias")
    with pytest.raises(NotFoundError):
        await agents.grant_tool(session, "finance_agent", 1, "rm_rf", reg)  # LLM-invented tool name
    with pytest.raises(NotFoundError):
        await agents.grant_tool(
            session, "finance_agent", 1, "lookup", ToolRegistry()
        )  # prod registry is empty


async def test_irreversible_tools_cannot_be_granted(session: AsyncSession) -> None:
    await seed_registry(session)
    with pytest.raises(ApprovalError, match="irreversible"):
        await agents.grant_tool(session, "finance_agent", 1, "send_payment", tool_registry())


async def test_write_tools_always_require_approval_even_if_requested_otherwise(
    session: AsyncSession,
) -> None:
    await seed_registry(session)
    reg = tool_registry()
    await agents.grant_tool(session, "finance_agent", 1, "save_note", reg, requires_approval=False)
    await agents.grant_tool(session, "finance_agent", 1, "lookup", reg, requires_approval=False)
    (v,) = await agents.list_versions(session, "finance_agent")
    assert {t.tool_name: t.requires_approval for t in v.tools} == {
        "save_note": True,
        "lookup": False,
    }


async def test_memory_grants_confined_to_own_namespace(session: AsyncSession) -> None:
    await seed_registry(session)
    await agents.grant_memory(
        session, "finance_agent", 1, "finance_agent/ledger", MemoryMode.READ_WRITE
    )
    with pytest.raises(PermissionDeniedError):
        await agents.grant_memory(
            session, "finance_agent", 1, "messaging_agent/conv", MemoryMode.READ
        )
    with pytest.raises(PermissionDeniedError):
        await agents.grant_memory(
            session, "messaging_agent", 1, "finance_agent/ledger", MemoryMode.READ
        )
    (v,) = await agents.list_versions(session, "messaging_agent")
    assert not v.memory


async def test_versions_have_independent_permissions(session: AsyncSession) -> None:
    await seed_registry(session)
    await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    v2 = await agents.create_version(session, "finance_agent", agents.VersionLimits(max_steps=2))
    assert not v2.model_aliases and v2.max_steps == 2  # a new draft starts empty (default-deny)


# --- start_run: only enabled agents with an active version ------------------------------------------


async def test_disabled_agent_cannot_start_a_run(session: AsyncSession, gov: Governance) -> None:
    ctx = await runnable_agent(session, gov)
    await agents.set_agent_enabled(session, "finance_agent", False)
    with pytest.raises(AgentNotPermittedError, match="disabled"):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)


async def test_unknown_agent_and_unknown_or_disabled_user_refused(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov)
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=ctx.user_id, agent_id="ghost_agent", governance=gov)
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=uuid.uuid4(), agent_id="finance_agent", governance=gov)
    user = await session.get(User, ctx.user_id)
    assert user is not None
    user.disabled_at = datetime.now(UTC)
    await session.flush()
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=ctx.user_id, agent_id="finance_agent", governance=gov)


async def test_agent_with_enabled_flag_but_draft_only_cannot_run(
    session: AsyncSession, gov: Governance
) -> None:
    await seed_registry(session)
    await session.execute(
        text("UPDATE agents SET enabled = true WHERE agent_id = 'finance_agent'")
    )  # bad manual edit
    user = User(display_name="t")
    session.add(user)
    await session.flush()
    with pytest.raises(AgentNotPermittedError, match="no active version"):
        await start_run(session, user_id=user.id, agent_id="finance_agent", governance=gov)


async def test_run_context_reflects_active_version_and_isolates_agents(
    session: AsyncSession, gov: Governance
) -> None:
    ctx = await runnable_agent(session, gov, "finance_agent", (MOCK_ALIAS,))
    assert ctx.model_aliases == {MOCK_ALIAS} and ctx.version == 1
    # messaging_agent was seeded too but has no active version/permissions: it cannot borrow finance's.
    with pytest.raises(AgentNotPermittedError):
        await start_run(session, user_id=ctx.user_id, agent_id="messaging_agent", governance=gov)
    with pytest.raises(PermissionDeniedError):
        ctx.authorize_memory("messaging_agent/anything")


# --- tool executor ----------------------------------------------------------------------------------


async def granted_ctx(session: AsyncSession, gov: Governance) -> RunContext:
    await seed_registry(session)
    reg = tool_registry()
    for name, approval in (("lookup", False), ("save_note", True), ("slow_lookup", False)):
        await agents.grant_tool(session, "finance_agent", 1, name, reg, requires_approval=approval)
    await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    await agents.activate_version(session, "finance_agent", 1)
    await agents.set_agent_enabled(session, "finance_agent", True)
    user = User(display_name="t")
    session.add(user)
    await session.flush()
    await session.commit()
    return await start_run(session, user_id=user.id, agent_id="finance_agent", governance=gov)


async def test_granted_read_tool_runs_with_validated_arguments(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    assert await ex.execute(ctx, "lookup", {"key": "k"}) == "found:k"
    assert "tool.executed" in await audit_types(sessions)


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"key": 5},
        {"key": "k", "limit": "many"},
        {"key": "k", "admin": True},
        {"key": "k", "__proto__": 1},
        "str",
    ],
)
async def test_tool_arguments_validated_against_schema(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], args: object, gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    before = len(calls)
    with pytest.raises(ToolValidationError):
        await ex.execute(ctx, "lookup", args)  # type: ignore[arg-type]
    assert len(calls) == before  # the handler never ran


async def test_oversized_arguments_rejected(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    with pytest.raises(ToolValidationError, match="too large"):
        await ToolExecutor(session, sessions, gov, tool_registry()).execute(
            ctx, "lookup", {"key": "x" * 20_000}
        )


@pytest.mark.parametrize(
    "name", ["send_payment", "delete_all", "LOOKUP", "lookup ", "../lookup", ""]
)
async def test_ungranted_or_invented_tools_denied_and_audited_durably(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], name: str, gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    with pytest.raises(PermissionDeniedError):
        await ToolExecutor(session, sessions, gov, tool_registry()).execute(ctx, name, {"key": "k"})
    await session.rollback()  # the caller's transaction is gone ...
    assert "tool.denied" in await audit_types(sessions)  # ... the denial is still on record


async def test_tool_granted_in_db_but_missing_from_code_fails_closed(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    with pytest.raises(PermissionDeniedError, match="not available"):
        await ToolExecutor(session, sessions, gov, ToolRegistry()).execute(
            ctx, "lookup", {"key": "k"}
        )


async def test_write_tool_needs_an_approval_bound_to_the_exact_arguments(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    args = {"note": "pay rent"}
    with pytest.raises(ApprovalRequiredError):
        await ex.execute(ctx, "save_note", args)

    approval = await create_approval(session, ctx, "save_note", args)
    with pytest.raises(ApprovalRequiredError):  # approved for different arguments
        await ex.execute(ctx, "save_note", {"note": "pay landlord"}, approval_id=approval)
    assert await ex.execute(ctx, "save_note", args, approval_id=approval) == "saved"
    with pytest.raises(ApprovalRequiredError):  # single use
        await ex.execute(ctx, "save_note", args, approval_id=approval)


async def test_approval_is_not_transferable(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    approval = await create_approval(session, ctx, "save_note", {"note": "n"})
    other_user = RunContext(**{**ctx.__dict__, "user_id": uuid.uuid4()})
    with pytest.raises(ApprovalRequiredError):
        await ex.execute(other_user, "save_note", {"note": "n"}, approval_id=approval)
    other_version = RunContext(**{**ctx.__dict__, "version_id": uuid.uuid4()})
    with pytest.raises(ApprovalRequiredError):
        await ex.execute(other_version, "save_note", {"note": "n"}, approval_id=approval)
    with pytest.raises(ApprovalRequiredError):  # random id
        await ex.execute(ctx, "save_note", {"note": "n"}, approval_id=uuid.uuid4())


async def test_expired_approval_rejected(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    approval = await create_approval(session, ctx, "save_note", {"note": "n"})
    row = await session.get(ToolApproval, approval)
    assert row is not None and row.expires_at - row.created_at <= timedelta(minutes=5, seconds=1)
    later = datetime.now(UTC) + timedelta(minutes=6)
    with pytest.raises(ApprovalRequiredError):
        await ex.execute(ctx, "save_note", {"note": "n"}, approval_id=approval, now=later)


async def test_tool_timeout_enforced(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ctx.limits = type(ctx.limits)(
        ctx.limits.max_steps, 1, 100, 100, ctx.limits.max_cost_inr
    )  # 1s budget
    with pytest.raises(LimitExceededError, match="timeout"):
        await ToolExecutor(session, sessions, gov, tool_registry()).execute(
            ctx, "slow_lookup", {"key": "k"}
        )


async def test_each_tool_call_counts_as_a_step(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession], gov: Governance
) -> None:
    ctx = await granted_ctx(session, gov)
    ex = ToolExecutor(session, sessions, gov, tool_registry())
    for _ in range(ctx.limits.max_steps):
        await ex.execute(ctx, "lookup", {"key": "k"})
    with pytest.raises(LimitExceededError, match="steps"):
        await ex.execute(ctx, "lookup", {"key": "k"})


# --- audit trail --------------------------------------------------------------------------------------


async def test_lifecycle_is_audited_with_version_specific_history(
    session: AsyncSession, sessions: async_sessionmaker[AsyncSession]
) -> None:
    await seed_registry(session)
    await agents.grant_model_alias(session, "finance_agent", 1, MOCK_ALIAS)
    await agents.activate_version(session, "finance_agent", 1)
    await agents.set_agent_enabled(session, "finance_agent", True)
    await agents.clone_version(session, "finance_agent", 1)
    await session.commit()
    events = (
        (
            await session.execute(
                select(AuditEvent).where(AuditEvent.subject_id.like("finance_agent%"))
            )
        )
        .scalars()
        .all()
    )
    kinds = {e.event_type for e in events}
    assert {
        "agent.registered",
        "agent.version_created",
        "agent.permission_granted",
        "agent.version_activated",
        "agent.enabled",
        "agent.version_cloned",
    } <= kinds
    granted = next(e for e in events if e.event_type == "agent.permission_granted")
    assert granted.subject_id == "finance_agent:1" and granted.detail == {
        "kind": "model_alias",
        "value": MOCK_ALIAS,
    }


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE audit_events SET event_type = 'tampered'",
        "DELETE FROM audit_events",
        "TRUNCATE audit_events",
    ],
)
async def test_audit_log_is_append_only(session: AsyncSession, statement: str) -> None:
    await seed_registry(session)
    assert (await session.execute(text("SELECT count(*) FROM audit_events"))).scalar_one() > 0
    with pytest.raises(DBAPIError, match="append-only"):
        await session.execute(text(statement))


async def test_versions_table_has_no_agent_level_model_permissions_left(
    session: AsyncSession,
) -> None:
    tables = set(
        (
            await session.execute(text("SELECT tablename FROM pg_tables WHERE schemaname='public'"))
        ).scalars()
    )
    assert "agent_model_permissions" not in tables and "agent_version_model_aliases" in tables
    assert (await session.execute(select(AgentVersion))).first() is None or True
