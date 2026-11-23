"""Memory namespace rules (pure functions, no I/O).

  <agent_id>/...   agent-specific memory. Only that agent, only with an explicit grant.
  user/...         user-specific memory shared across agents ONLY where a version is explicitly granted it.
  anything else    invalid. There is no way to grant one agent another agent's namespace.

Third-party conversation memory must be isolated per platform AND conversation:
  <agent_id>/<platform>/<conversation_id>   (an opaque id; never a display name)
"""

import re

NAMESPACE = re.compile(r"^[a-z][a-z0-9_]*(/[a-z0-9][a-z0-9_.:-]*)*$")
USER_ROOT = "user"
MAX_LEN = 128


class NamespaceError(ValueError):
    pass


def validate_format(namespace: str) -> None:
    if len(namespace) > MAX_LEN or not NAMESPACE.match(namespace) or ".." in namespace:
        raise NamespaceError(f"invalid memory namespace {namespace!r}")


def owner(namespace: str) -> str:
    return namespace.split("/", 1)[0]


def segments(namespace: str) -> list[str]:
    return namespace.split("/")


def is_within(namespace: str, granted: str) -> bool:
    """Path-boundary prefix match: 'a/b/c' is within 'a/b', but 'a/bc' is not."""
    return namespace == granted or namespace.startswith(granted + "/")


def require_conversation_scope(namespace: str) -> None:
    """Third-party-derived memory must name a platform and a conversation (>= 3 segments)."""
    if len(segments(namespace)) < 3:
        raise NamespaceError(
            "third-party conversation memory needs a namespace of the form <agent>/<platform>/<conversation_id>"
        )


def like_prefix(namespace: str) -> str:
    """Escape a namespace for use in `LIKE <result> || '/%'`."""
    return namespace.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
