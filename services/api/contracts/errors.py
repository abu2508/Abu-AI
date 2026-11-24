"""The two error envelopes the API already emits (documented, not changed).

* `{"error": {"code", "message"}}`: orchestrator and domain errors. `code` is a stable machine code.
* `{"detail": "<text>"}`: authentication, middleware (rate limit, size) and infrastructure failures.
The two different 429 meanings share a status: the rate limiter answers `{"detail": ...}` with `Retry-After`, while
`spending_blocked` / `limit_exceeded` answer with the error envelope. Clients branch on the body shape.
"""

from pydantic import Field

from services.api.contracts.common import Contract, UuidStr

ORCHESTRATOR_ERROR_CODES = (
    "invalid_request",
    "idempotency_conflict",
    "request_in_progress",
    "kill_switch_active",
    "governance_unavailable",
    "governance_refused",
    "permission_denied",
    "spending_blocked",
    "limit_exceeded",
    "model_unavailable",
    "memory_unavailable",
    "timeout",
    "internal_error",
    "malformed_output",
    "provider_error",
)
DOMAIN_ERROR_CODES = (
    "already_checked_in",
    "already_imported",
    "conversation_full",
    "credit_limit_required",
    "import_failed",
    "invalid_category",
    "invalid_channel",
    "invalid_date",
    "invalid_format",
    "invalid_hours",
    "invalid_loan",
    "invalid_range",
    "invalid_timezone",
    "not_found",
    "not_shared",
)


class ErrorBody(Contract):
    code: str = Field(
        description="Stable machine code. Open set: clients must tolerate unknown codes. Known orchestrator codes: "
        + ", ".join(ORCHESTRATOR_ERROR_CODES)
        + ". Known domain codes: "
        + ", ".join(DOMAIN_ERROR_CODES)
        + "."
    )
    message: str = Field(
        description="Safe, content-free text. Clients should show their own wording."
    )


class ErrorEnvelope(Contract):
    error: ErrorBody


class RunFailedEnvelope(ErrorEnvelope):
    """A governed run that ended without a usable answer: carries ids so the run can be found in history."""

    run_id: UuidStr
    execution_id: UuidStr | None
    trace_id: str | None


class DetailEnvelope(Contract):
    detail: str
