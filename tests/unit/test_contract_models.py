"""B3: the documentation-only response contracts, tested offline against SYNTHETIC fixtures.

No database, no network. The golden bodies were recorded from the unchanged backend with invented data.
"""

import ast
import copy
import re
import uuid
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from pydantic import ValidationError

from agents.tools import SPECS
from services.api import openapi_export
from services.api.contracts.coach import CoachBriefing, HabitsTool
from services.api.contracts.errors import (
    DOMAIN_ERROR_CODES,
    ORCHESTRATOR_ERROR_CODES,
    DetailEnvelope,
    ErrorEnvelope,
    RunFailedEnvelope,
)
from services.api.contracts.finance import FinanceSummary, InsightOut, UpcomingItem
from services.api.contracts.messaging import AnalyseTool
from services.api.contracts.tools import ToolResults
from services.api.main import create_app
from tests.contracts.support import load_golden, validate
from tests.integration.helpers import make_settings
from tests.unit.test_http_hardening import _walk

GOLDEN = load_golden()
U = str(uuid.UUID(int=7))


def bodies(prefix: str) -> list[tuple[str, Any]]:
    return [
        (k, v["body"]) for k, v in GOLDEN.items() if k.startswith(prefix) and v["status"] == 200
    ]


# ---- routes ------------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(("name", "body"), bodies("route.") + bodies("killswitch."))
def test_route_bodies_match_their_contracts(name: str, body: Any) -> None:
    if name.endswith("finance.summary") or "finance.summary" in name:
        validate(FinanceSummary, body)
    elif name.endswith("finance.insights"):
        validate(list[InsightOut], body)
    elif "finance.upcoming" in name:
        validate(list[UpcomingItem], body)
    else:
        validate(CoachBriefing, body)


# ---- every tool result ---------------------------------------------------------------------------------------------
def tool_payloads() -> list[tuple[str, Any]]:
    out = []
    for k, v in GOLDEN.items():
        data = v["body"].get("data") if k.startswith("run.") and v["status"] == 200 else None
        if isinstance(data, dict):
            out.append((k, data))
    return out


@pytest.mark.parametrize(("name", "data"), tool_payloads())
def test_orchestrator_data_matches_tool_results(name: str, data: Any) -> None:
    validate(ToolResults, data)


def test_the_golden_scenario_exercises_all_18_tools_and_the_model_names_exactly_those_tools() -> (
    None
):
    seen = {t for _, data in tool_payloads() for t in data}
    assert seen == set(SPECS), set(SPECS) ^ seen
    aliases = {f.alias for f in ToolResults.model_fields.values()}
    assert aliases == set(SPECS)


def test_tool_errors_and_empty_data_are_valid() -> None:
    validate(ToolResults, {"messaging.reply_suggestions": {"error": "no_shared_conversation"}})
    validate(ToolResults, {"finance.summary": {"error": "result_too_large"}})
    validate(ToolResults, {})


# ---- nullable, optional and empty variants -------------------------------------------------------------------------
SUMMARY: dict[str, Any] = {
    "start": "2026-11-01",
    "end": "2026-11-30",
    "income": "0.00",
    "expense": "0.00",
    "net": "0.00",
    "savings_rate_pct": None,
    "expense_by_category": [],
    "income_by_category": [],
    "pending_review_count": 0,
}
BRIEFING: dict[str, Any] = {
    "date": "2026-11-24",
    "events": [],
    "plan": {
        "blocks": [],
        "unscheduled": [],
        "overloaded": False,
        "planned_minutes": 0,
        "free_minutes": 0,
    },
    "habits_open": [],
    "streaks_at_risk": [],
    "goals_needing_attention": [],
    "reminders_due": [],
    "mood_trend": {"checkins": 0, "avg_mood": None, "avg_energy": None},
}


def test_empty_and_null_variants() -> None:
    validate(FinanceSummary, SUMMARY)  # no income: savings_rate_pct is null; empty lists
    validate(
        FinanceSummary, {**SUMMARY, "expense_by_category": [["rent", "-5.00"]]}
    )  # refund makes a negative
    validate(list[InsightOut], [])
    validate(list[UpcomingItem], [])
    validate(CoachBriefing, BRIEFING)
    validate(
        CoachBriefing,
        {
            **BRIEFING,
            "reminders_due": [
                {
                    "id": U,
                    "text": "t (demo)",
                    "remind_at": "2026-11-24T05:00:00+00:00",
                    "event_id": None,
                    "task_id": None,
                }
            ],
        },
    )
    validate(
        CoachBriefing,
        {
            **BRIEFING,
            "reminders_due": [
                {
                    "id": U,
                    "text": "t (demo)",
                    "remind_at": "2026-11-24T05:00:00Z",
                    "event_id": U,
                    "task_id": U,
                }
            ],
        },
    )


def test_analysis_variants_empty_conversation_and_unreliable_timestamps() -> None:
    base = {
        "message_count": 0,
        "instruction_like_messages": 0,
        "conversation_id": U,
        "platform": "whatsapp",
    }
    validate(AnalyseTool, base)  # empty conversation carries only the four required keys
    full = next(
        d["messaging.analyse"]
        for _, d in tool_payloads()
        if "messaging.analyse" in d and "median_my_response_minutes" in d["messaging.analyse"]
    )
    no_median = {k: v for k, v in full.items() if not k.startswith("median_")}
    validate(AnalyseTool, no_median)  # unreliable timestamps: the median keys are absent
    validate(
        AnalyseTool,
        {
            **full,
            "median_my_response_minutes": None,
            "waiting_hours": None,
            "last_message_intent": None,
        },
    )
    validate(
        AnalyseTool, {**full, "my_style": {"avg_words": 4, "uses_emoji": False}}
    )  # an int mean is valid JSON


def test_habits_are_a_discriminated_union_on_unit() -> None:
    days = {
        "id": U,
        "title": "t (demo)",
        "cadence": "daily",
        "target_per_week": 7,
        "current_streak": 1,
        "longest_streak": 2,
        "unit": "days",
        "done_today": False,
        "due_today": True,
        "completion_rate_30d": 10.0,
    }
    weeks = {
        "id": U,
        "title": "w (demo)",
        "cadence": "weekly",
        "target_per_week": 3,
        "current_streak": 0,
        "unit": "weeks",
        "this_week": 1,
        "done_today": False,
        "completion_rate_30d": None,
    }
    validate(HabitsTool, {"habits": [days, weeks]})
    validate(HabitsTool, {"habits": []})
    with pytest.raises(ValidationError):
        validate(
            HabitsTool, {"habits": [{**weeks, "longest_streak": 2}]}
        )  # weekly habits have no longest_streak
    with pytest.raises(ValidationError):
        validate(HabitsTool, {"habits": [{k: v for k, v in days.items() if k != "due_today"}]})


# ---- unexpected shapes are rejected (the drift alarm) -----------------------------------------------------------------
def test_unexpected_shapes_are_rejected() -> None:
    bad = [
        {**SUMMARY, "surprise": 1},  # extra field
        {k: v for k, v in SUMMARY.items() if k != "net"},  # missing field
        {**SUMMARY, "income": 0.0},  # float money
        {**SUMMARY, "income": "1,000.00"},  # not a decimal string
        {**SUMMARY, "pending_review_count": "0"},  # string where int expected
        {**SUMMARY, "expense_by_category": [{"category": "rent", "amount": "1.00"}]},  # reshaped
    ]
    for body in bad:
        with pytest.raises(ValidationError):
            validate(FinanceSummary, body)
    with pytest.raises(ValidationError):
        validate(ToolResults, {"finance.unknown_tool": {}})
    with pytest.raises(ValidationError):
        validate(ToolResults, {"finance.summary": {**SUMMARY, "surprise": 1}})
    with pytest.raises(ValidationError):
        validate(list[InsightOut], [{"code": "x", "severity": "fatal", "params": {}, "text": "t"}])
    with pytest.raises(ValidationError):
        validate(
            list[InsightOut], [{"code": "x", "severity": "info", "params": {"a": 1.5}, "text": "t"}]
        )  # float param


def test_insight_params_are_a_scalar_map_and_codes_are_open() -> None:
    validate(
        list[InsightOut],
        [
            {
                "code": "budget_exceeded",
                "severity": "alert",
                "params": {"category": "groceries", "limit": "1000.00", "spent": "1830.50"},
                "text": "t",
            }
        ],
    )
    validate(
        list[InsightOut],
        [
            {
                "code": "a_code_added_later",
                "severity": "info",
                "params": {"n": 3, "x": None},
                "text": "t",
            }
        ],
    )


# ---- errors --------------------------------------------------------------------------------------------------------
def test_error_envelopes() -> None:
    validate(ErrorEnvelope, {"error": {"code": "not_found", "message": "not found"}})
    validate(
        RunFailedEnvelope,
        {
            "error": {"code": "timeout", "message": "m"},
            "run_id": U,
            "execution_id": None,
            "trace_id": "abc",
        },
    )
    validate(DetailEnvelope, {"detail": "invalid credentials"})
    for model, body in [
        (ErrorEnvelope, {"error": {"code": "x"}}),
        (ErrorEnvelope, {"error": {"code": "x", "message": "m"}, "extra": 1}),
        (DetailEnvelope, {"detail": ["list"]}),
        (RunFailedEnvelope, {"error": {"code": "x", "message": "m"}}),
    ]:
        with pytest.raises(ValidationError):
            validate(model, body)


def test_every_recorded_error_body_matches_an_envelope_and_a_documented_code() -> None:
    known = set(ORCHESTRATOR_ERROR_CODES) | set(DOMAIN_ERROR_CODES)
    for name, rec in GOLDEN.items():
        body = rec["body"]
        if rec["status"] < 400:
            continue
        if "error" in body:
            validate(RunFailedEnvelope if "run_id" in body else ErrorEnvelope, body)
            assert body["error"]["code"] in known, (name, body["error"]["code"])
        elif isinstance(body.get("detail"), str):
            validate(DetailEnvelope, body)
        else:
            assert isinstance(
                body["detail"], list
            )  # FastAPI validation errors keep their own documented shape


# ---- OpenAPI wiring and scope guards -------------------------------------------------------------------------------------
COVERED = [
    ("/v1/finance/summary", "GET"),
    ("/v1/finance/insights", "GET"),
    ("/v1/finance/upcoming", "GET"),
    ("/v1/coach/briefing", "GET"),
]


def test_covered_routes_stay_documentation_only() -> None:
    app = create_app(make_settings())
    routes = {
        (r.path, m): r
        for r in _walk(app.routes)
        if isinstance(r, APIRoute)
        for m in (r.methods or ())
    }
    for key in [*COVERED, ("/v1/orchestrator/run", "POST")]:
        assert key in routes, key
    for key in COVERED:
        r = routes[key]
        # `-> Any` already makes FastAPI report `typing.Any` (no validation, filtering or re-serialisation).
        # B3 must leave that exactly as it was: never a concrete model.
        assert r.response_model in (None, Any), (key, "must not enforce a response model")
        assert r.status_code in (None, 200)
    # the orchestrator's own (pre-existing) typed response model is untouched
    assert routes[("/v1/orchestrator/run", "POST")].response_model is not None


def test_published_schema_has_the_contracts_and_error_envelopes() -> None:
    import json

    s = json.loads(openapi_export.render())
    comps = s["components"]["schemas"]
    assert set(ToolResults.model_json_schema(by_alias=True)["properties"]) == set(SPECS)
    data = comps["OrchestratorResponse"]["properties"]["data"]
    assert data["anyOf"][0] == {"$ref": "#/components/schemas/ToolResults"}
    for path, ref in [
        ("/v1/finance/summary", "FinanceSummary"),
        ("/v1/coach/briefing", "CoachBriefing"),
    ]:
        ok = s["paths"][path]["get"]["responses"]["200"]["content"]["application/json"]["schema"]
        assert ok["$ref"].endswith(ref)
    for path in (p for p, _ in COVERED):
        resp = s["paths"][path]["get"]["responses"]
        assert {"200", "401", "429", "503"} <= set(resp), path
    run = s["paths"]["/v1/orchestrator/run"]["post"]["responses"]
    assert {"200", "401", "403", "409", "422", "429", "500", "502", "503", "504"} <= set(run)
    assert comps["OrchestratorResponse"]["properties"]["status"]["enum"] == [
        "completed",
        "clarification_needed",
        "unsupported",
        "failed",
    ]


def test_schema_generation_is_idempotent() -> None:
    assert openapi_export.render() == openapi_export.render()


def test_the_contracts_package_is_not_imported_by_request_handling_code() -> None:
    root = Path(__file__).resolve().parents[2]
    allowed = {"services/api/finance.py", "services/api/coach.py", "services/api/main.py"}
    offenders = []
    for p in (
        list((root / "services").rglob("*.py"))
        + list((root / "core").rglob("*.py"))
        + list((root / "domain").rglob("*.py"))
        + list((root / "agents").rglob("*.py"))
    ):
        rel = p.relative_to(root).as_posix()
        if rel.startswith("services/api/contracts/") or rel in allowed:
            continue
        if "services.api.contracts" in p.read_text(encoding="utf-8"):
            offenders.append(rel)
    assert offenders == []


def test_covered_handlers_still_return_any_unchanged() -> None:
    root = Path(__file__).resolve().parents[2]
    for rel, names in {
        "services/api/finance.py": {"summary", "insights", "upcoming"},
        "services/api/coach.py": {"briefing"},
    }.items():
        tree = ast.parse((root / rel).read_text(encoding="utf-8"))
        found = {
            n.name: ast.unparse(n.returns) if n.returns else None
            for n in ast.walk(tree)
            if isinstance(n, ast.AsyncFunctionDef) and n.name in names
        }
        assert found == dict.fromkeys(names, "Any"), (rel, found)


# ---- fixture privacy ---------------------------------------------------------------------------------------------------
PRIVACY_PATTERNS = [
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "email"),
    (re.compile(r"(?<!\d)(?:\+91[ -]?)?[6-9]\d{9}(?!\d)"), "mobile-number-like"),
    (re.compile(r"\b\d{13,19}\b"), "card/account-like digit run"),
    (re.compile(r"https?://"), "URL"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\."), "JWT"),
    (re.compile(r"\b(?:rt|enr)_[A-Za-z0-9_-]{16,}"), "backend token shape"),
]


def test_fixtures_contain_only_synthetic_data() -> None:
    base = Path(__file__).resolve().parents[1] / "contracts"
    for f in [*base.rglob("*.json"), base / "scenario.py"]:
        text = f.read_text(encoding="utf-8")
        for rx, what in PRIVACY_PATTERNS:
            assert not rx.search(text), (f.name, what)
    # every invented name and label is marked as a demo value
    text = (base / "golden" / "golden.json").read_text(encoding="utf-8")
    for label in (
        "Demo car loan",
        "Demo card",
        "Demo streaming",
        "Run a demo 10k",
        "Meditate",
    ):
        assert f"{label}" in text
    assert "(demo)" in text


def test_the_scenario_and_golden_do_not_mutate_between_loads() -> None:
    a, b = load_golden(), load_golden()
    assert a == b and copy.deepcopy(a) == a
