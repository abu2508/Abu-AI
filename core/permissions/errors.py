class PermissionDeniedError(Exception):
    """Default-deny: the action is not granted to this agent version."""


class AgentNotPermittedError(PermissionDeniedError):
    """Agent is unknown, disabled, or has no active version."""


class LimitExceededError(Exception):
    """A per-run limit (steps, time, tokens, cost) would be or was exceeded."""


class ApprovalRequiredError(Exception):
    """The operation needs a valid, single-use human approval bound to these exact arguments."""


class ToolValidationError(Exception):
    """Tool input failed schema validation."""
