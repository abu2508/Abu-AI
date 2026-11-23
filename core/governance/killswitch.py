"""Global kill switch. Persistent (DB row), fail-closed, audited.

Blocks: new executions, model calls, tool calls, paid-provider reservations. It does NOT block
reads, audit access, authentication, session revocation or disabling agents.

Who may do what:
  activate    operator CLI  or  authenticated app      (records who, when, why, which interface)
  deactivate  operator CLI ONLY
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import Actor, record_event, record_event_durable
from core.governance.errors import GovernanceUnavailableError, KillSwitchActiveError
from core.permissions.errors import PermissionDeniedError
from database.schemas import KillSwitchState

Factory = async_sessionmaker[AsyncSession]
MAX_REASON = 500


@dataclass(frozen=True)
class KillSwitchSnapshot:
    active: bool
    reason: str | None
    activated_at: datetime | None
    activated_by_type: str | None
    activated_by_user_id: uuid.UUID | None
    activated_via: str | None


def _snap(row: KillSwitchState) -> KillSwitchSnapshot:
    return KillSwitchSnapshot(
        row.active,
        row.reason,
        row.activated_at,
        row.activated_by_type,
        row.activated_by_user_id,
        row.activated_via,
    )


async def read_state(factory: Factory) -> KillSwitchSnapshot:
    """Always a fresh read of committed state. Any failure or missing row => GovernanceUnavailable."""
    try:
        async with factory() as s:
            row = (
                await s.execute(select(KillSwitchState).where(KillSwitchState.id == 1))
            ).scalar_one_or_none()
    except Exception as exc:
        raise GovernanceUnavailableError("kill-switch state could not be read") from exc
    if row is None:
        raise GovernanceUnavailableError("kill-switch state row is missing")
    return _snap(row)


async def require_inactive(factory: Factory) -> None:
    snap = await read_state(factory)
    if snap.active:
        raise KillSwitchActiveError("kill switch is active: new executions are blocked")


def check_inactive_in_tx(row: KillSwitchState | None) -> None:
    """For callers that already hold the row in an open transaction (e.g. spend reservation)."""
    if row is None:
        raise GovernanceUnavailableError("kill-switch state row is missing")
    if row.active:
        raise KillSwitchActiveError("kill switch is active: new executions are blocked")


def _reason(reason: str) -> str:
    reason = reason.strip()
    if not reason or len(reason) > MAX_REASON:
        raise ValueError(f"a reason of 1-{MAX_REASON} characters is required")
    return reason


async def activate(factory: Factory, actor: Actor, reason: str) -> KillSwitchSnapshot:
    if actor.type not in ("admin", "user"):
        raise PermissionDeniedError(
            "only an operator or an authenticated user may activate the kill switch"
        )
    reason = _reason(reason)
    via = "cli" if actor.type == "admin" else "api"
    async with factory() as s, s.begin():
        row = (
            await s.execute(
                select(KillSwitchState).where(KillSwitchState.id == 1).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise GovernanceUnavailableError("kill-switch state row is missing")
        if row.active:
            # Keep the ORIGINAL activation record; note the repeat attempt in the audit trail.
            await record_event(
                s,
                actor,
                "kill_switch.activate_ignored",
                "governance",
                "kill_switch",
                {"reason": reason, "via": via},
            )
        else:
            row.active = True
            row.reason = reason
            row.activated_at = datetime.now(UTC)
            row.activated_by_type = actor.type
            row.activated_by_user_id = actor.user_id
            row.activated_via = via
            row.updated_at = row.activated_at
            await record_event(
                s,
                actor,
                "kill_switch.activated",
                "governance",
                "kill_switch",
                {"reason": reason, "via": via},
            )
        await s.flush()
        return _snap(row)


async def deactivate(factory: Factory, actor: Actor, reason: str) -> KillSwitchSnapshot:
    """Operator CLI only. Any other actor is refused AND the attempt is audited durably."""
    if actor.type != "admin":
        await record_event_durable(
            factory,
            actor,
            "governance.privileged_action_denied",
            "governance",
            "kill_switch",
            {"action": "kill_switch.deactivate"},
        )
        raise PermissionDeniedError("only the operator CLI may deactivate the kill switch")
    reason = _reason(reason)
    async with factory() as s, s.begin():
        row = (
            await s.execute(
                select(KillSwitchState).where(KillSwitchState.id == 1).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            raise GovernanceUnavailableError("kill-switch state row is missing")
        if not row.active:
            await record_event(
                s,
                actor,
                "kill_switch.deactivate_ignored",
                "governance",
                "kill_switch",
                {"reason": reason},
            )
        else:
            row.active = False
            row.updated_at = datetime.now(UTC)
            await record_event(
                s,
                actor,
                "kill_switch.deactivated",
                "governance",
                "kill_switch",
                {
                    "reason": reason,
                    "was_activated_via": row.activated_via or "",
                    "was_reason": row.reason or "",
                },
            )
            row.reason = row.activated_at = row.activated_by_type = None
            row.activated_by_user_id = row.activated_via = None
        await s.flush()
        return _snap(row)


async def deny_privileged_action(
    factory: Factory, actor: Actor, action: str, via: str = "api"
) -> None:
    """Durably audit an attempt to perform an operator-only action from an app token."""
    await record_event_durable(
        factory,
        actor,
        "governance.privileged_action_denied",
        "governance",
        action,
        {"action": action, "via": via},
    )
