"""Append-only audit trail. Records WHO did WHAT to WHICH subject; never secrets or raw payloads.

Two entry points:
  record_event(session, ...)        in the caller's transaction (rolls back with it)
  record_event_durable(factory, ...) in its own transaction, so DENIALS survive a rollback
"""

import re
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from database.schemas import AuditEvent

ActorType = Literal["user", "admin", "system"]

_FORBIDDEN_KEY = re.compile(
    r"token|secret|password|code|key|args|prompt|content|authorization", re.I
)
_MAX_VALUE = 200


@dataclass(frozen=True)
class Actor:
    type: ActorType
    user_id: uuid.UUID | None = None
    session_id: uuid.UUID | None = None

    @classmethod
    def admin(cls) -> "Actor":
        return cls("admin")

    @classmethod
    def system(cls) -> "Actor":
        return cls("system")

    @classmethod
    def user(cls, user_id: uuid.UUID, session_id: uuid.UUID | None = None) -> "Actor":
        return cls("user", user_id, session_id)


def sanitize_detail(detail: dict[str, Any] | None) -> dict[str, Any]:
    """Keep only small scalar values under non-sensitive-looking keys."""
    clean: dict[str, Any] = {}
    for key, value in (detail or {}).items():
        if _FORBIDDEN_KEY.search(key):
            continue
        if isinstance(value, bool | int | float) or value is None:
            clean[key] = value
        elif isinstance(value, str):
            clean[key] = value[:_MAX_VALUE]
        else:
            clean[key] = str(value)[:_MAX_VALUE]
    return clean


SYSTEM = Actor("system")
ADMIN = Actor("admin")


def _event(
    actor: Actor,
    event_type: str,
    subject_type: str,
    subject_id: str,
    detail: dict[str, Any] | None,
) -> AuditEvent:
    return AuditEvent(
        actor_type=actor.type,
        actor_user_id=actor.user_id,
        actor_session_id=actor.session_id,
        event_type=event_type,
        subject_type=subject_type,
        subject_id=str(subject_id)[:128],
        detail=sanitize_detail(detail),
    )


async def record_event(
    session: AsyncSession,
    actor: Actor,
    event_type: str,
    subject_type: str,
    subject_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    session.add(_event(actor, event_type, subject_type, subject_id, detail))
    await session.flush()


async def record_event_durable(
    factory: async_sessionmaker[AsyncSession],
    actor: Actor,
    event_type: str,
    subject_type: str,
    subject_id: str,
    detail: dict[str, Any] | None = None,
) -> None:
    async with factory() as s, s.begin():
        s.add(_event(actor, event_type, subject_type, subject_id, detail))
