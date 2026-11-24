"""Publishes the documentation-only contracts through the generated OpenAPI document (B3, decision D2).

`OrchestratorResponse.data` stays `dict[str, Any]` at runtime. Only the PUBLISHED schema is refined: the post-processor
below runs once when the schema is first built (export, or the authenticated dev route) and never touches a request.
"""

from typing import Any

from fastapi import FastAPI
from pydantic import BaseModel

from services.api.contracts.coach import (
    BlockOut,
    CoachBriefing,
    EventOut,
    GoalsTool,
    HabitsTool,
    PlanTool,
    ProgressTool,
    ReviewTool,
)
from services.api.contracts.errors import DetailEnvelope, ErrorEnvelope, RunFailedEnvelope
from services.api.contracts.finance import (
    BudgetsTool,
    CardsTool,
    CashflowTool,
    FinanceSummary,
    InsightsTool,
    LoansTool,
    PendingTool,
    SubscriptionsTool,
    UpcomingTool,
)
from services.api.contracts.messaging import AnalyseTool, MessagingSummaryTool, SuggestionsTool
from services.api.contracts.tools import ToolError, ToolResults

REF = "#/components/schemas/{model}"

# Every model published as its own component (nested models are hoisted automatically).
PUBLISHED: tuple[type[BaseModel], ...] = (
    ToolResults,
    ToolError,
    FinanceSummary,
    CashflowTool,
    BudgetsTool,
    InsightsTool,
    UpcomingTool,
    LoansTool,
    CardsTool,
    SubscriptionsTool,
    PendingTool,
    AnalyseTool,
    SuggestionsTool,
    MessagingSummaryTool,
    PlanTool,
    CoachBriefing,
    ReviewTool,
    ProgressTool,
    HabitsTool,
    GoalsTool,
    EventOut,
    BlockOut,
    ErrorEnvelope,
    RunFailedEnvelope,
    DetailEnvelope,
)

DETAIL, ERROR, FAILED = "DetailEnvelope", "ErrorEnvelope", "RunFailedEnvelope"
VALIDATION = "HTTPValidationError"

# status -> envelopes that can carry it. Documentation only; the tests drive each documented case for real.
GET_ERRORS: dict[str, tuple[str, ...]] = {"401": (DETAIL,), "429": (DETAIL,), "503": (DETAIL,)}
RUN_ERRORS: dict[str, tuple[str, ...]] = {
    "401": (DETAIL,),
    "403": (ERROR, FAILED),
    "409": (ERROR,),
    "422": (VALIDATION, ERROR),
    "429": (DETAIL, ERROR, FAILED),
    "500": (ERROR, FAILED),
    "502": (FAILED,),
    "503": (ERROR, FAILED, DETAIL),
    "504": (FAILED, ERROR),
}
ERROR_TABLE: dict[tuple[str, str], dict[str, tuple[str, ...]]] = {
    ("/v1/finance/summary", "get"): GET_ERRORS,
    ("/v1/finance/insights", "get"): GET_ERRORS,
    ("/v1/finance/upcoming", "get"): GET_ERRORS,
    ("/v1/coach/briefing", "get"): GET_ERRORS,
    ("/v1/orchestrator/run", "post"): RUN_ERRORS,
}
DESCRIPTIONS = {
    "401": "Not authenticated or session ended (`detail` envelope, WWW-Authenticate: Bearer).",
    "403": "Refused by governance or permissions (error envelope).",
    "409": "Idempotency-Key reused with a different request, or still running (error envelope).",
    "422": "Validation error (FastAPI) or `invalid_request` (error envelope).",
    "429": "Rate limit (`detail` envelope + Retry-After) or spending/per-run limit (error envelope).",
    "500": "Internal error (error envelope).",
    "502": "The model output was malformed or the provider failed (`malformed_output`, `provider_error`; run-failed envelope).",
    "503": "Kill switch or service unavailable (error envelope or `detail`).",
    "504": "Run timeout (error envelope).",
}


def _ref(name: str) -> dict[str, str]:
    return {"$ref": REF.format(model=name)}


def _hoist(schemas: dict[str, Any], model: type[BaseModel]) -> None:
    js = model.model_json_schema(ref_template=REF, by_alias=True)
    for name, definition in js.pop("$defs", {}).items():
        schemas.setdefault(name, definition)
    schemas.setdefault(model.__name__, js)


def augment(schema: dict[str, Any]) -> dict[str, Any]:
    schemas: dict[str, Any] = schema.setdefault("components", {}).setdefault("schemas", {})
    for model in PUBLISHED:
        _hoist(schemas, model)
    # 1. The published type of OrchestratorResponse.data (runtime keeps dict[str, Any]).
    orch = schemas.get("OrchestratorResponse")
    if orch is not None:
        prop = orch["properties"]["data"]
        prop["anyOf"] = [_ref("ToolResults"), {"type": "null"}]
        prop["description"] = (
            "Deterministic results from the user's own records, keyed by tool name (money as strings)."
        )
    # 2. Error envelopes on the covered routes.
    for (path, method), table in ERROR_TABLE.items():
        op = schema["paths"][path][method]
        for status, names in table.items():
            options = [_ref(n) for n in names]
            content = {
                "application/json": {
                    "schema": options[0] if len(options) == 1 else {"oneOf": options}
                }
            }
            op["responses"][status] = {"description": DESCRIPTIONS[status], "content": content}
    return schema


def install(app: FastAPI) -> None:
    """Wrap `app.openapi` so the schema is augmented exactly once. Request handling is unaffected."""
    original = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            return augment(original())
        schema: dict[str, Any] = app.openapi_schema
        return schema

    app.openapi = openapi  # type: ignore[method-assign]
