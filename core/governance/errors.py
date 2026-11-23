from typing import Literal


class GovernanceError(Exception):
    """Base for governance refusals. Execution must stop; none of these are retryable by the agent."""


class KillSwitchActiveError(GovernanceError):
    """The global kill switch is active: no new executions, model calls or tool calls."""


class GovernanceUnavailableError(GovernanceError):
    """Governance state could not be read. We FAIL CLOSED: treat it as if the kill switch is on."""


class PaidSpendingBlockedError(GovernanceError):
    """Paid spending is not currently permitted (disabled, frozen, zero budget, rate unset/stale)."""


class BudgetExceededError(GovernanceError):
    def __init__(
        self, scope: Literal["run", "day", "month"], limit: str, used: str, requested: str
    ) -> None:
        self.scope = scope
        super().__init__(
            f"{scope} paid budget would be exceeded (limit {limit}, used {used}, requested {requested} INR)"
        )


class DuplicateCallError(GovernanceError):
    """This exact call (same idempotency key) was already attempted; the provider is not called twice."""
