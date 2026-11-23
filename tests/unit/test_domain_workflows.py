"""Workflow registry rules and deterministic planning for the domain agents (no database)."""

from datetime import date

import pytest
from pydantic import BaseModel, ConfigDict

from agents.summaries import summarise
from agents.tools import AGENT_TOOLS, SPECS, build_registry
from agents.workflows import domain_registry, domain_tool_registry, extract_period
from core.orchestrator.types import Intent, ToolCall, WorkflowSpec
from core.orchestrator.workflows import MockWorkflowHandler, WorkflowRegistry
from core.permissions.tools import ToolRegistry, ToolRisk, ToolSpec

TODAY = date(2024, 5, 20)


def test_every_tool_is_read_only_strict_and_has_no_user_argument() -> None:
    reg = domain_tool_registry()
    assert set(reg.names()) == set(SPECS)
    for name in reg.names():
        spec = reg.get(name)
        assert spec is not None and spec.risk is ToolRisk.READ
        assert spec.input_model.model_config.get("extra") == "forbid"
        assert not {"user_id", "user", "agent_id", "token"} & set(spec.input_model.model_fields)
    assert not [
        n
        for n in SPECS
        if any(w in n for w in ("pay", "send", "transfer", "write", "delete", "post"))
    ]


def test_agent_tool_sets_are_disjoint_by_domain() -> None:
    assert all(t.startswith("finance.") for t in AGENT_TOOLS["finance_agent"])
    assert all(t.startswith("messaging.") for t in AGENT_TOOLS["messaging_agent"])
    assert all(t.startswith("coach.") for t in AGENT_TOOLS["daily_coach_agent"])
    assert AGENT_TOOLS["general_agent"] == ()


def test_registry_refuses_unknown_non_read_or_missing_tools() -> None:
    reg = domain_registry()
    assert {s.name for s in reg.all()} >= {
        "finance.budgets",
        "finance.loans",
        "coach.briefing",
        "coach.review",
        "messaging.conversation_summary",
    }
    for spec in reg.all():
        assert set(spec.tool_allowlist) <= set(AGENT_TOOLS[spec.agent_id]), (
            spec.name
        )  # a workflow only names its OWN agent's tools

    def mk(tools: tuple[str, ...]) -> WorkflowSpec:
        return WorkflowSpec(
            name="x.y",
            intent=Intent.FINANCE,
            agent_id="finance_agent",
            model_alias="test-mock",
            handler=MockWorkflowHandler("r"),
            description="d",
            tool_allowlist=tools,
            memory_roots=("finance_agent",),
        )

    with pytest.raises(ValueError, match="unknown tool"):
        WorkflowRegistry(domain_tool_registry()).register(mk(("finance.pay",)))
    with pytest.raises(ValueError, match="unknown tool"):
        WorkflowRegistry().register(mk(("finance.summary",)))  # no tool registry at all

    class In(BaseModel):
        model_config = ConfigDict(extra="forbid")

    async def h(ctx: object, args: object) -> None: ...

    risky = ToolRegistry()
    risky.register(ToolSpec("finance.write_thing", "w", In, ToolRisk.WRITE, h))
    with pytest.raises(ValueError, match="not read-only"):
        WorkflowRegistry(risky).register(mk(("finance.write_thing",)))
    with pytest.raises(ValueError, match="too many tools"):
        WorkflowRegistry(domain_tool_registry()).register(
            mk(tuple(AGENT_TOOLS["finance_agent"][:5]))
        )


def test_handlers_only_plan_tools_from_their_own_allowlist() -> None:
    for spec in domain_registry().all():
        calls = spec.handler.plan_tools(spec, "anything in March tomorrow formal", None)
        assert {c.name for c in calls} <= set(spec.tool_allowlist)
    assert domain_registry().get(Intent.GENERAL).tool_allowlist == ()  # type: ignore[union-attr]


@pytest.mark.parametrize(
    ("text", "want"),
    [
        ("how did I spend in March", {"year": 2024, "month": 3}),
        ("spending in march 2023", {"year": 2023, "month": 3}),
        ("what about December", {"year": 2023, "month": 12}),
        ("last month", {"year": 2024, "month": 4}),
        ("this month", {}),
    ],
)
def test_period_extraction(text: str, want: dict[str, int]) -> None:
    assert extract_period(text, TODAY) == want
    assert extract_period("last month", date(2024, 1, 5)) == {"year": 2023, "month": 12}


def test_planner_arguments() -> None:
    reg = domain_registry()
    fin = reg.get(Intent.FINANCE, "budgets")
    assert fin is not None
    assert fin.handler.plan_tools(fin, "budget in march 2023", None)[0] == ToolCall(
        "finance.budget_status", {"year": 2023, "month": 3}
    )
    msg = reg.get(Intent.MESSAGING)
    assert msg is not None
    import uuid

    cid = uuid.uuid4()
    first = msg.handler.plan_tools(msg, "reply in a formal tone", cid)[0]
    assert first.name == "messaging.reply_suggestions" and first.args == {
        "conversation_id": str(cid),
        "tone": "formal",
    }
    coach = reg.get(Intent.COACHING, "planning")
    assert coach is not None and coach.handler.plan_tools(coach, "plan tomorrow", None)[0].args == {
        "offset_days": 1
    }


def test_summaries_use_templates_and_exact_figures() -> None:
    text = summarise(
        {
            "finance.summary": {
                "income": "50000.00",
                "expense": "2250.00",
                "net": "47750.00",
                "pending_review_count": 2,
            },
            "finance.budget_status": {
                "budgets": [
                    {
                        "category": "groceries",
                        "spent": "1200.50",
                        "limit": "1000.00",
                        "status": "over",
                    }
                ]
            },
            "messaging.reply_suggestions": {"drafts": [{"text": "Sounds good, I'm in."}]},
            "messaging.analyse": {"error": "not_shared"},
        }
    )
    assert text is not None
    assert (
        "Income ₹50000.00, spending ₹2250.00, net ₹47750.00." in text
        and "2 transaction(s) await your review" in text
    )
    assert (
        "groceries: ₹1200.50 of ₹1000.00 (over)." in text
        and "nothing is sent" in text
        and "messaging.analyse: unavailable (not_shared)." in text
    )
    assert summarise({}) is None


def test_registry_without_deps_fails_closed() -> None:
    assert build_registry(None).get("finance.summary") is not None
