"""Idempotency + run history on `orchestrator_runs`.

* The request is stored only as a KEYED hash (HMAC) so a replay with a different body is detected (409)
  without ever persisting what the user typed.
* A response is stored ONLY when the caller supplied an Idempotency-Key, envelope-encrypted, and scrubbed
  after a TTL. Without a key nothing but metadata is kept ("do not save every conversation").
* One live claim per (user, key): concurrent duplicates get 409 while the first is running; a FAILED run
  with the same body may be retried under the same key.
"""

import hashlib
import hmac
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, cast

from pydantic import SecretStr
from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.memory.crypto import Envelope, aad_for
from core.orchestrator.types import OrchestratorError, OrchestratorResponse
from database.schemas import OrchestratorRun

Factory = async_sessionmaker[AsyncSession]
STALE_RUNNING = timedelta(minutes=10)
PURPOSE = "orchestrator_response"


def keyed_hash(key: SecretStr, *parts: str) -> str:
    return hmac.new(
        key.get_secret_value().encode(), "\x1f".join(parts).encode(), hashlib.sha256
    ).hexdigest()


@dataclass(frozen=True)
class Claim:
    run_id: uuid.UUID
    replay: OrchestratorRun | None  # a finished run to replay, if any


class RunStore:
    def __init__(
        self, factory: Factory, envelope: Envelope, hash_key: SecretStr, ttl_hours: int
    ) -> None:
        self._f, self._env, self._key, self._ttl = factory, envelope, hash_key, ttl_hours

    def hashes(
        self, user_id: uuid.UUID, message: str, idem_key: str | None
    ) -> tuple[str | None, str]:
        uid = str(user_id)
        return (
            keyed_hash(self._key, "idem", uid, idem_key) if idem_key else None,
            keyed_hash(self._key, "req", uid, message),
        )

    async def claim(
        self, user_id: uuid.UUID, origin: str, key_hash: str | None, request_hash: str
    ) -> Claim:
        run_id = uuid.uuid4()
        async with self._f() as s, s.begin():
            res = await s.execute(
                insert(OrchestratorRun)
                .values(
                    id=run_id,
                    user_id=user_id,
                    idempotency_key_hash=key_hash,
                    request_hash=request_hash,
                    status="running",
                    origin=origin,
                )
                .on_conflict_do_nothing()
                .returning(OrchestratorRun.id)
            )
            if res.scalar_one_or_none() is not None:
                return Claim(run_id, None)
            existing = (
                await s.execute(
                    select(OrchestratorRun)
                    .where(
                        OrchestratorRun.user_id == user_id,
                        OrchestratorRun.idempotency_key_hash == key_hash,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            if existing.request_hash != request_hash:
                raise OrchestratorError(
                    "idempotency_conflict",
                    "this Idempotency-Key was used with a different request",
                    409,
                )
            now = datetime.now(UTC)
            if existing.status == "completed":
                s.expunge(existing)
                return Claim(existing.id, existing)
            stale = existing.status == "running" and now - existing.created_at > STALE_RUNNING
            if existing.status == "failed" or stale:  # retryable: reuse the same run id
                existing.status, existing.error_code, existing.completed_at = "running", None, None
                existing.outcome, existing.execution_id = None, None
                existing.created_at = now
                return Claim(existing.id, None)
            raise OrchestratorError(
                "request_in_progress", "a request with this Idempotency-Key is still running", 409
            )

    async def finish(
        self,
        run_id: uuid.UUID,
        user_id: uuid.UUID,
        *,
        status: str,
        intent: str | None,
        agent_id: str | None,
        workflow: str | None,
        outcome: str | None,
        error_code: str | None,
        execution_id: uuid.UUID | None,
        response: OrchestratorResponse | None,
        store_response: bool,
    ) -> None:
        blob = expires = None
        if response is not None and store_response:
            blob = self._env.seal(
                response.model_dump_json().encode(), aad_for(user_id, run_id, PURPOSE)
            )
            expires = datetime.now(UTC) + timedelta(hours=self._ttl)
        async with self._f() as s, s.begin():
            await s.execute(
                update(OrchestratorRun)
                .where(OrchestratorRun.id == run_id, OrchestratorRun.user_id == user_id)
                .values(
                    status=status,
                    intent=intent,
                    agent_id=agent_id,
                    workflow=workflow,
                    outcome=outcome,
                    error_code=error_code,
                    execution_id=execution_id,
                    response_enc=blob,
                    response_expires_at=expires,
                    completed_at=datetime.now(UTC),
                )
            )

    def open_response(self, run: OrchestratorRun) -> OrchestratorResponse | None:
        """Decrypt a stored response if present and unexpired; None means expired/never stored."""
        if run.response_enc is None or run.response_expires_at is None:
            return None
        if run.response_expires_at <= datetime.now(UTC):
            return None
        raw = self._env.open(run.response_enc, aad_for(run.user_id, run.id, PURPOSE))
        return OrchestratorResponse.model_validate_json(raw)

    async def get(self, user_id: uuid.UUID, run_id: uuid.UUID) -> OrchestratorRun | None:
        async with self._f() as s:
            row = (
                await s.execute(
                    select(OrchestratorRun).where(
                        OrchestratorRun.id == run_id, OrchestratorRun.user_id == user_id
                    )
                )
            ).scalar_one_or_none()
            if row is not None:
                s.expunge(row)
            return row

    async def list(
        self, user_id: uuid.UUID, limit: int = 20, before: datetime | None = None
    ) -> list[OrchestratorRun]:
        q = select(OrchestratorRun).where(OrchestratorRun.user_id == user_id)
        if before:
            q = q.where(OrchestratorRun.created_at < before)
        async with self._f() as s:
            rows = list(
                (
                    await s.execute(q.order_by(OrchestratorRun.created_at.desc()).limit(limit))
                ).scalars()
            )
            for r in rows:
                s.expunge(r)
            return rows

    async def scrub_expired(self) -> int:
        async with self._f() as s, s.begin():
            res = await s.execute(
                update(OrchestratorRun)
                .where(
                    OrchestratorRun.response_expires_at <= datetime.now(UTC),
                    OrchestratorRun.response_enc.is_not(None),
                )
                .values(response_enc=None)
            )
            return cast(CursorResult[Any], res).rowcount or 0

    async def purge(self, retention_days: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(days=retention_days)
        async with self._f() as s, s.begin():
            res = await s.execute(
                delete(OrchestratorRun).where(OrchestratorRun.created_at < cutoff)
            )
            return cast(CursorResult[Any], res).rowcount or 0
