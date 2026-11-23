class RegistryError(Exception):
    """Base class for registry validation failures (safe to show to operators)."""


class NotFoundError(RegistryError):
    pass


class DuplicateError(RegistryError):
    pass


class ApprovalError(RegistryError):
    """A model or policy does not meet the conditions for the requested state change."""
