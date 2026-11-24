"""B3: the orchestrator's exception -> (HTTP status, public code) table is exactly what the error contract documents.

Pure unit test of the existing `_classify_error`; it documents today's behaviour and fails if a status or code changes.
"""

import pytest

from core.governance.errors import (
    BudgetExceededError,
    GovernanceError,
    GovernanceUnavailableError,
    KillSwitchActiveError,
    PaidSpendingBlockedError,
)
from core.orchestrator.orchestrator import _classify_error
from core.orchestrator.types import OrchestratorError
from core.permissions.errors import LimitExceededError, PermissionDeniedError
from services.api.contracts.errors import ORCHESTRATOR_ERROR_CODES


@pytest.mark.parametrize(
    ("exc", "status", "code"),
    [
        (KillSwitchActiveError("x"), 503, "kill_switch_active"),
        (GovernanceUnavailableError("x"), 503, "governance_unavailable"),
        (BudgetExceededError("day", "1", "2", "3"), 429, "spending_blocked"),
        (PaidSpendingBlockedError("x"), 429, "spending_blocked"),
        (GovernanceError("x"), 403, "governance_refused"),
        (PermissionDeniedError("x"), 403, "permission_denied"),
        (LimitExceededError("a per-run limit"), 429, "limit_exceeded"),
        (LimitExceededError("run timeout exceeded"), 504, "timeout"),
        (TimeoutError(), 504, "timeout"),
        (OrchestratorError("malformed_output", "m", 502), 502, "malformed_output"),
        (RuntimeError("anything else"), 500, "internal_error"),
    ],
)
def test_classification_matches_the_documented_codes(
    exc: BaseException, status: int, code: str
) -> None:
    _, _, public_code, http, _ = _classify_error(exc)
    assert (http, public_code) == (status, code)
    assert public_code in ORCHESTRATOR_ERROR_CODES
