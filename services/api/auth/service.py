"""Enrollment, refresh-token rotation, session listing and revocation.

Failure modes are deliberately indistinguishable to callers (one generic AuthError); the real
reason goes to the audit log. Functions flush but do not commit: the caller owns the transaction,
except that an AuthError with persist=True means "commit what was written before raising"
(used for refresh-token reuse detection, which must stick even though the request fails).
"""

import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core.audit.service import Actor, record_event
from database.schemas import AuthSession, EnrollmentCode, RefreshToken, User
from services.api.auth import tokens
from services.api.config import Settings

LAST_USED_RESOLUTION = timedelta(minutes=1)
# A lost refresh response may be retried ONCE, within this window, by presenting the same
# Idempotency-Key as the original request. See refresh() for why this does not weaken reuse detection.
RETRY_WINDOW = timedelta(seconds=30)


class AuthError(Exception):
    def __init__(self, message: str = "invalid credentials", *, persist: bool = False) -> None:
        super().__init__(message)
        self.persist = persist


class NotFoundError(Exception):
    pass


@dataclass(frozen=True)
class Principal:
    """The verified identity attached to every authenticated request."""

    user_id: uuid.UUID
    session_id: uuid.UUID


@dataclass(frozen=True)
class TokenPair:
    access_token: str
    expires_in: int
    refresh_token: str
    session_id: uuid.UUID


def _now(now: datetime | None) -> datetime:
    return now or datetime.now(UTC)


async def create_user(session: AsyncSession, display_name: str, actor: Actor) -> User:
    user = User(display_name=display_name)
    session.add(user)
    await session.flush()
    await record_event(session, actor, "user.created", "user", str(user.id))
    return user


async def create_enrollment_code(
    session: AsyncSession,
    settings: Settings,
    user_id: uuid.UUID,
    actor: Actor,
    label: str | None = None,
    now: datetime | None = None,
) -> tuple[str, datetime]:
    """Returns the plaintext code ONCE; only its hash is stored."""
    _, hash_key = settings.require_auth_secrets()
    user = await session.get(User, user_id)
    if user is None or user.disabled_at is not None:
        raise NotFoundError("unknown or disabled user")
    code = tokens.new_secret("enr", 16)  # 128 bits
    expires = _now(now) + timedelta(minutes=settings.enrollment_code_ttl_minutes)
    row = EnrollmentCode(
        user_id=user_id,
        code_hash=tokens.hash_secret(hash_key, code),
        label=label,
        expires_at=expires,
    )
    session.add(row)
    await session.flush()
    await record_event(session, actor, "enrollment.code_created", "enrollment_code", str(row.id))
    return code, expires


async def _issue_pair(
    session: AsyncSession, settings: Settings, auth_session: AuthSession, now: datetime
) -> TokenPair:
    _, hash_key = settings.require_auth_secrets()
    refresh = tokens.new_secret("rt")
    expires = min(now + timedelta(days=settings.refresh_token_ttl_days), auth_session.expires_at)
    row = RefreshToken(
        session_id=auth_session.id,
        token_hash=tokens.hash_secret(hash_key, refresh),
        expires_at=expires,
    )
    session.add(row)
    await session.flush()
    access, ttl = tokens.issue_access_token(settings, auth_session.user_id, auth_session.id, now)
    return TokenPair(access, ttl, refresh, auth_session.id)


async def enroll(
    session: AsyncSession,
    settings: Settings,
    code: str,
    device_name: str,
    platform: str,
    now: datetime | None = None,
) -> TokenPair:
    now = _now(now)
    _, hash_key = settings.require_auth_secrets()
    row = (
        await session.execute(
            select(EnrollmentCode)
            .where(EnrollmentCode.code_hash == tokens.hash_secret(hash_key, code))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await record_event(
            session,
            Actor.system(),
            "enrollment.failed",
            "enrollment_code",
            "unknown",
            {"reason": "unknown_code"},
        )
        raise AuthError(persist=True)
    user = await session.get(User, row.user_id)
    reason = (
        "already_used"
        if row.used_at is not None
        else "expired"
        if row.expires_at <= now
        else "user_disabled"
        if user is None or user.disabled_at is not None
        else None
    )
    if reason or user is None:
        await record_event(
            session,
            Actor.system(),
            "enrollment.failed",
            "enrollment_code",
            str(row.id),
            {"reason": reason},
        )
        raise AuthError(persist=True)
    auth_session = AuthSession(
        user_id=user.id,
        device_name=device_name,
        platform=platform,
        expires_at=now + timedelta(days=settings.session_max_days),
        last_used_at=now,
    )
    session.add(auth_session)
    await session.flush()
    row.used_at = now
    row.used_by_session_id = auth_session.id
    pair = await _issue_pair(session, settings, auth_session, now)
    await record_event(
        session,
        Actor.user(user.id, auth_session.id),
        "auth.enrolled",
        "auth_session",
        str(auth_session.id),
        {"platform": platform, "device": device_name},
    )
    return pair


async def revoke_session(
    session: AsyncSession,
    session_id: uuid.UUID,
    actor: Actor,
    reason: str,
    now: datetime | None = None,
) -> None:
    now = _now(now)
    result = await session.execute(
        update(AuthSession)
        .where(AuthSession.id == session_id, AuthSession.revoked_at.is_(None))
        .values(revoked_at=now, revoked_reason=reason)
    )
    if result.rowcount:  # type: ignore[attr-defined]
        await record_event(
            session,
            actor,
            "auth.session_revoked",
            "auth_session",
            str(session_id),
            {"reason": reason},
        )


def _idem_hash(hash_key: SecretStr, key: str | None) -> str | None:
    return tokens.hash_secret(hash_key, "idem:" + key) if key else None


async def refresh(
    session: AsyncSession,
    settings: Settings,
    refresh_token: str,
    now: datetime | None = None,
    idempotency_key: str | None = None,
) -> TokenPair:
    """Rotate a refresh token.

    Idempotent retry (lost response): if the SAME token comes back with the SAME Idempotency-Key
    that rotated it, within RETRY_WINDOW, at most once, and its child has not been used yet, we
    issue a replacement child and mark the previous child as used (superseded). Reuse detection
    stays intact: the superseded child, if ever presented, revokes the whole session; a retry with
    a different/missing key, after the window, a second retry, or after the child was used, is
    treated as reuse exactly as before. An attacker holding only a stolen token does not know the
    key; one who captured the whole request gets at most one replacement inside 30s, and the real
    client's next refresh then trips the reuse detector.
    """
    now = _now(now)
    _, hash_key = settings.require_auth_secrets()
    row = (
        await session.execute(
            select(RefreshToken)
            .where(RefreshToken.token_hash == tokens.hash_secret(hash_key, refresh_token))
            .with_for_update()
        )
    ).scalar_one_or_none()
    if row is None:
        await record_event(
            session,
            Actor.system(),
            "auth.refresh_failed",
            "refresh_token",
            "unknown",
            {"reason": "unknown_token"},
        )
        raise AuthError(persist=True)
    auth_session = await session.get(AuthSession, row.session_id, with_for_update=True)
    if auth_session is None:
        raise AuthError
    actor = Actor.user(auth_session.user_id, auth_session.id)
    user = await session.get(User, auth_session.user_id)

    retry = (
        row.used_at is not None
        and idempotency_key is not None
        and row.rotation_key_hash is not None
        and hmac.compare_digest(row.rotation_key_hash, _idem_hash(hash_key, idempotency_key) or "")
        and row.retry_count == 0
        and now - row.used_at <= RETRY_WINDOW
        and row.replaced_by_id is not None
        and auth_session.revoked_at is None
        and auth_session.expires_at > now
        and user is not None
        and user.disabled_at is None
    )
    if retry:
        child = await session.get(RefreshToken, row.replaced_by_id, with_for_update=True)
        if child is not None and child.used_at is None:
            child.used_at = now  # superseded: presenting it later counts as reuse
            pair = await _issue_pair(session, settings, auth_session, now)
            new_row = (
                await session.execute(
                    select(RefreshToken).where(
                        RefreshToken.token_hash == tokens.hash_secret(hash_key, pair.refresh_token)
                    )
                )
            ).scalar_one()
            row.replaced_by_id = new_row.id
            row.retry_count = 1
            auth_session.last_used_at = now
            await record_event(
                session, actor, "auth.refresh_retried", "auth_session", str(auth_session.id)
            )
            return pair

    if row.used_at is not None:
        # A rotated-out token came back: either the legitimate client lost a response or the
        # token was stolen. We cannot tell, so kill the whole session; the user re-enrolls.
        await revoke_session(session, auth_session.id, actor, "refresh_token_reuse", now)
        await record_event(
            session, actor, "auth.refresh_reuse_detected", "auth_session", str(auth_session.id)
        )
        raise AuthError(persist=True)
    reason = (
        "session_revoked"
        if auth_session.revoked_at is not None
        else "session_expired"
        if auth_session.expires_at <= now
        else "token_expired"
        if row.expires_at <= now
        else "user_disabled"
        if user is None or user.disabled_at is not None
        else None
    )
    if reason:
        await record_event(
            session,
            actor,
            "auth.refresh_failed",
            "auth_session",
            str(auth_session.id),
            {"reason": reason},
        )
        raise AuthError(persist=True)

    row.used_at = now
    row.rotation_key_hash = _idem_hash(hash_key, idempotency_key)
    pair = await _issue_pair(session, settings, auth_session, now)
    new_row = (
        await session.execute(
            select(RefreshToken).where(
                RefreshToken.token_hash == tokens.hash_secret(hash_key, pair.refresh_token)
            )
        )
    ).scalar_one()
    row.replaced_by_id = new_row.id
    auth_session.last_used_at = now
    await record_event(session, actor, "auth.refreshed", "auth_session", str(auth_session.id))
    return pair


async def authenticate(
    session: AsyncSession, settings: Settings, access_token: str, now: datetime | None = None
) -> Principal:
    """Verify signature/claims, then confirm the session is still alive (instant revocation)."""
    now = _now(now)
    try:
        claims = tokens.decode_access_token(settings, access_token)
    except tokens.InvalidTokenError:
        raise AuthError from None
    auth_session = await session.get(AuthSession, claims.session_id)
    if (
        auth_session is None
        or auth_session.user_id != claims.user_id
        or auth_session.revoked_at is not None
        or auth_session.expires_at <= now
    ):
        raise AuthError
    user = await session.get(User, claims.user_id)
    if user is None or user.disabled_at is not None:
        raise AuthError
    if auth_session.last_used_at is None or now - auth_session.last_used_at > LAST_USED_RESOLUTION:
        auth_session.last_used_at = now
    return Principal(claims.user_id, claims.session_id)


async def list_sessions(session: AsyncSession, user_id: uuid.UUID) -> list[AuthSession]:
    rows = await session.execute(
        select(AuthSession)
        .where(AuthSession.user_id == user_id)
        .order_by(AuthSession.created_at.desc())
    )
    return list(rows.scalars())


async def revoke_user_session(
    session: AsyncSession, principal: Principal, session_id: uuid.UUID, reason: str = "user_revoked"
) -> None:
    """A user may revoke only their own sessions (others look nonexistent)."""
    target = await session.get(AuthSession, session_id)
    if target is None or target.user_id != principal.user_id:
        raise NotFoundError("session not found")
    await revoke_session(
        session, session_id, Actor.user(principal.user_id, principal.session_id), reason
    )
