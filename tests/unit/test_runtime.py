import uuid
from decimal import Decimal

import pytest
from pydantic import BaseModel, ConfigDict, ValidationError

from core.agent_registry.service import VersionLimits, validate_namespace
from core.audit.service import sanitize_detail
from core.permissions.errors import LimitExceededError, PermissionDeniedError
from core.permissions.runtime import RunContext, RunLimits, estimate_cost_inr
from core.permissions.tools import ToolRegistry, ToolRisk, ToolSpec, args_hash
from database.schemas import MemoryMode


class Clock:
    def __init__(self) -> None:
        self.t = 0.0

    def __call__(self) -> float:
        return self.t


def ctx(clock: Clock | None = None, **over: object) -> RunContext:
    limits = RunLimits(
        max_steps=3,
        timeout_seconds=10,
        max_input_tokens=100,
        max_output_tokens=50,
        max_cost_inr=Decimal("1"),
    )
    base: dict[str, object] = {
        "user_id": uuid.uuid4(),
        "agent_id": "finance_agent",
        "version_id": uuid.uuid4(),
        "version": 1,
        "limits": limits,
        "model_aliases": frozenset({"general"}),
        "tools": {"read_x": False, "write_x": True},
        "memory": {
            "finance_agent/ledger": MemoryMode.READ_WRITE,
            "finance_agent/notes": MemoryMode.READ,
        },
    }
    base.update(over)
    if clock:
        base["clock"] = clock
    return RunContext(**base)  # type: ignore[arg-type]


# --- default-deny permissions -------------------------------------------------------------


def test_everything_denied_by_default() -> None:
    c = ctx(model_aliases=frozenset(), tools={}, memory={})
    with pytest.raises(PermissionDeniedError):
        c.authorize_alias("general")
    with pytest.raises(PermissionDeniedError):
        c.authorize_tool("read_x")
    with pytest.raises(PermissionDeniedError):
        c.authorize_memory("finance_agent/ledger")


def test_alias_and_tool_grants() -> None:
    c = ctx()
    c.authorize_alias("general")
    with pytest.raises(PermissionDeniedError):
        c.authorize_alias("gemini-3.1-flash-lite")  # a raw provider id is not an alias
    assert c.authorize_tool("read_x") is False and c.authorize_tool("write_x") is True
    with pytest.raises(PermissionDeniedError):
        c.authorize_tool("delete_everything")


def test_memory_modes_and_hierarchy() -> None:
    c = ctx()
    c.authorize_memory("finance_agent/ledger", write=True)
    c.authorize_memory("finance_agent/ledger/2026", write=True)  # child of a granted namespace
    c.authorize_memory("finance_agent/notes")
    with pytest.raises(PermissionDeniedError, match="read-only"):
        c.authorize_memory("finance_agent/notes", write=True)
    with pytest.raises(PermissionDeniedError):
        c.authorize_memory("finance_agent/other")
    with pytest.raises(PermissionDeniedError):
        c.authorize_memory("finance_agent/ledgerX")  # prefix match must be on a path boundary


@pytest.mark.parametrize(
    "ns",
    [
        "messaging_agent/conv",
        "daily_coach_agent",
        "finance_agent/../messaging_agent/x",
        "shared/x",
        "",
    ],
)
def test_cross_agent_memory_denied_even_with_bad_grant_rows(ns: str) -> None:
    # Simulate corrupted data: the DB (wrongly) holds a grant for another agent's namespace.
    c = ctx(memory={"messaging_agent/conv": MemoryMode.READ_WRITE, ns: MemoryMode.READ_WRITE})
    with pytest.raises(PermissionDeniedError):
        c.authorize_memory(ns, write=True)


@pytest.mark.parametrize("ns", ["messaging_agent/x", "shared/x", "daily_coach_agent"])
def test_cannot_grant_other_agents_namespace(ns: str) -> None:
    with pytest.raises(PermissionDeniedError):
        validate_namespace("finance_agent", ns)


@pytest.mark.parametrize(
    "ns",
    ["Finance_agent/x", "finance_agent/../x", "finance_agent//x", "finance_agent/ x", "x" * 200],
)
def test_malformed_namespaces_rejected(ns: str) -> None:
    with pytest.raises((ValueError, PermissionDeniedError)):
        validate_namespace("finance_agent", ns)


def test_own_namespaces_accepted() -> None:
    for ns in ("finance_agent", "finance_agent/ledger", "finance_agent/accounts/hdfc:1234"):
        validate_namespace("finance_agent", ns)


# --- limits ---------------------------------------------------------------------------------


def test_max_steps() -> None:
    c = ctx()
    for _ in range(3):
        c.begin_step()
    with pytest.raises(LimitExceededError, match="steps"):
        c.begin_step()


def test_timeout_uses_deadline() -> None:
    clock = Clock()
    c = ctx(clock)
    c.begin_step()
    clock.t = 9.0
    assert c.remaining_seconds() == pytest.approx(1.0)
    clock.t = 10.5
    with pytest.raises(LimitExceededError, match="timeout"):
        c.begin_step()


def test_token_and_cost_budgets() -> None:
    c = ctx()
    assert (c.remaining_input_tokens, c.remaining_output_tokens) == (100, 50)
    c.record_usage(60, 20, Decimal("0.4"))
    assert (c.remaining_input_tokens, c.remaining_output_tokens, c.remaining_cost_inr) == (
        40,
        30,
        Decimal("0.6"),
    )
    c.record_usage(500, 500, Decimal("5"))
    assert (c.remaining_input_tokens, c.remaining_output_tokens, c.remaining_cost_inr) == (
        0,
        0,
        Decimal(0),
    )


def test_cost_estimate() -> None:
    cost = estimate_cost_inr(1_000_000, 500_000, Decimal("0.25"), Decimal("1.5"), Decimal("100"))
    assert cost == Decimal("100")  # (0.25 + 0.75) USD * 100
    assert (
        estimate_cost_inr(10**9, 10**9, Decimal(0), Decimal(0), Decimal("100")) == 0
    )  # free is free


# --- version limits (hard ceilings) -----------------------------------------------------------


@pytest.mark.parametrize(
    "bad",
    [
        {"max_steps": 0},
        {"max_steps": 51},
        {"timeout_seconds": 301},
        {"max_run_input_tokens": 200_001},
        {"max_run_output_tokens": 0},
        {"max_run_cost_inr": "-1"},
        {"max_run_cost_inr": "51"},
        {"unknown_field": 1},
        {"config": {"k": "x" * 9000}},
    ],
)
def test_version_limits_ceilings(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        VersionLimits(**bad)


def test_default_limits_are_free_only() -> None:
    assert VersionLimits().max_run_cost_inr == 0


# --- tool registry + audit sanitising ---------------------------------------------------------


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    n: int


class Loose(BaseModel):
    n: int


async def _h(ctx: RunContext, args: object) -> None:
    return None


def test_tool_registry_rules() -> None:
    reg = ToolRegistry()
    reg.register(ToolSpec("lookup", "d", Strict, ToolRisk.READ, _h))
    assert reg.get("lookup") and reg.get("nope") is None and reg.names() == ["lookup"]
    with pytest.raises(ValueError, match="already"):
        reg.register(ToolSpec("lookup", "d", Strict, ToolRisk.READ, _h))
    with pytest.raises(ValueError, match="extra"):
        reg.register(
            ToolSpec("loose", "d", Loose, ToolRisk.READ, _h)
        )  # would accept smuggled fields
    for bad in ("Bad", "a b", "../x", "x" * 100, ""):
        with pytest.raises(ValueError, match="invalid"):
            reg.register(ToolSpec(bad, "d", Strict, ToolRisk.READ, _h))


def test_args_hash_binds_exact_arguments() -> None:
    assert args_hash("t", {"a": 1, "b": 2}) == args_hash("t", {"b": 2, "a": 1})
    assert args_hash("t", {"a": 1}) != args_hash("t", {"a": 2})
    assert args_hash("t", {"a": 1}) != args_hash("u", {"a": 1})


def test_audit_detail_drops_secrets_and_payloads() -> None:
    clean = sanitize_detail(
        {
            "reason": "x",
            "refresh_token": "rt_abc",
            "api_key": "k",
            "tool_args": {"a": 1},
            "prompt": "p",
            "enrollment_code": "c",
            "authorization": "Bearer x",
            "n": 3,
            "long": "y" * 500,
            "obj": {"a": 1},
        }
    )
    assert set(clean) == {"reason", "n", "long", "obj"}
    assert len(clean["long"]) == 200
