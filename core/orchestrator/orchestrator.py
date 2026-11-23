"""Orchestrator service: the governed entry points around the graph.

Entry points (both share ONE pipeline, `_execute`; there is no other way to run a workflow):
  * run_user_request(user_id, message, idempotency_key)  - HTTP; user_id comes from authentication ONLY.
  * run_scheduled_task(user_id, task)                    - trusted code; takes an ENUM, never free text.

Governance order on every run:
  kill switch (here, before anything is claimed or replayed) -> idempotency claim -> classify -> route ->
  start_run (kill switch again, agent registry, version, execution record) -> permission engine ->
  memory (grants) -> ModelGateway (kill switch, alias, limits, spending, model registry) -> validation ->
  execution record finished + durable audit.
"""

import asyncio
import logging
import time
import uuid
from enum import StrEnum
from typing import Any

from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.governance.errors import (
    BudgetExceededError,
    GovernanceError,
    GovernanceUnavailableError,
    KillSwitchActiveError,
    PaidSpendingBlockedError,
)
from core.governance.service import Governance
from core.memory.crypto import DecryptionError, KeyUnavailableError
from core.memory.runtime import MemoryRuntime
from core.model_router.adapters import MockAdapter, ProviderAdapter
from core.model_router.gateway import ModelGateway
from core.model_router.router import NoRouteError
from core.orchestrator.classifier import RuleBasedClassifier
from core.orchestrator.graph import RECURSION_LIMIT, Deps, build_graph
from core.orchestrator.idempotency import RunStore
from core.orchestrator.types import (
    Classifier,
    ContextInfo,
    OrchestratorError,
    OrchestratorResponse,
    RunFailed,
    RunScope,
    Usage,
)
from core.orchestrator.workflows import Router, WorkflowRegistry, default_registry
from core.permissions.errors import LimitExceededError, PermissionDeniedError

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 4000
HARD_TIMEOUT_SECONDS = (
    125.0  # outer backstop; each workflow also enforces its own (<= 120s) timeout
)


class ScheduledTask(StrEnum):
    """Scheduled work is chosen from this fixed set; free text can never reach the orchestrator this way."""

    DAILY_BRIEFING = "daily_briefing"
    WEEKLY_REVIEW = "weekly_review"


_SCHEDULED_TEXT = {
    ScheduledTask.DAILY_BRIEFING: "Prepare my morning briefing and plan my day",
    ScheduledTask.WEEKLY_REVIEW: "Prepare my weekly review of goals and tasks",
}


def _classify_error(exc: BaseException) -> tuple[str, str, str, int, str]:
    """-> (execution status, reason code, public code, http status, public message). Never echoes content."""
    if isinstance(exc, KillSwitchActiveError):
        return (
            "blocked",
            "kill_switch",
            "kill_switch_active",
            503,
            "execution is currently disabled",
        )
    if isinstance(exc, GovernanceUnavailableError):
        return (
            "blocked",
            "governance_unavailable",
            "governance_unavailable",
            503,
            "execution is currently unavailable",
        )
    if isinstance(exc, BudgetExceededError | PaidSpendingBlockedError):
        return "blocked", "budget", "spending_blocked", 429, "spending limits prevent this request"
    if isinstance(exc, GovernanceError):
        return "blocked", "governance", "governance_refused", 403, "governance refused this request"
    if isinstance(exc, PermissionDeniedError):
        return (
            "blocked",
            "permission_denied",
            "permission_denied",
            403,
            "this request is not permitted",
        )
    if isinstance(exc, LimitExceededError):
        if "timeout" in str(exc):
            return "timeout", "timeout", "timeout", 504, "the request took too long"
        return (
            "limit_exceeded",
            "limit_exceeded",
            "limit_exceeded",
            429,
            "a per-run limit was reached",
        )
    if isinstance(exc, TimeoutError):
        return "timeout", "timeout", "timeout", 504, "the request took too long"
    if isinstance(exc, NoRouteError):
        return "failed", "no_route", "model_unavailable", 503, "no permitted model is available"
    if isinstance(exc, KeyUnavailableError | DecryptionError):
        return (
            "failed",
            "memory_unavailable",
            "memory_unavailable",
            503,
            "memory is temporarily unavailable",
        )
    if isinstance(exc, OrchestratorError):
        return "failed", exc.code[:60], exc.code, exc.http_status, exc.message
    return "failed", "internal_error", "internal_error", 500, "the request could not be completed"


class Orchestrator:
    def __init__(
        self,
        factory: async_sessionmaker[AsyncSession],
        governance: Governance,
        memory: MemoryRuntime | None,
        *,
        token_hash_key: SecretStr,
        classifier: Classifier | None = None,
        registry: WorkflowRegistry | None = None,
        adapters: dict[str, ProviderAdapter] | None = None,
        response_ttl_hours: int = 24,
        thread_days: int = 7,
    ) -> None:
        self._f = factory
        self._gov = governance
        self._memory = memory
        self._classifier: Classifier = classifier or RuleBasedClassifier()
        self.registry = registry or default_registry()
        self._router = Router(self.registry)
        # ONLY the in-process mock adapter by default: no provider is reachable from here.
        self._adapters: dict[str, ProviderAdapter] = (
            adapters if adapters is not None else {"mock": MockAdapter()}
        )
        self._thread_days = thread_days
        envelope = memory.envelope if memory is not None else None
        if envelope is None:
            raise RuntimeError("the orchestrator needs the memory runtime (response encryption)")
        self.store = RunStore(factory, envelope, token_hash_key, response_ttl_hours)

    # ---- entry points -------------------------------------------------------------------
    async def run_user_request(
        self,
        user_id: uuid.UUID,
        message: str,
        *,
        idempotency_key: str | None = None,
        trace_id: str | None = None,
    ) -> OrchestratorResponse:
        return await self._execute(user_id, message, "user", idempotency_key, trace_id)

    async def run_scheduled_task(
        self, user_id: uuid.UUID, task: ScheduledTask
    ) -> OrchestratorResponse:
        if not isinstance(task, ScheduledTask):
            raise TypeError("scheduled work must be a ScheduledTask")
        return await self._execute(user_id, _SCHEDULED_TEXT[task], "scheduled", None, None)

    # ---- the one pipeline -----------------------------------------------------------------
    async def _execute(
        self,
        user_id: uuid.UUID,
        message: str,
        origin: str,
        idem_key: str | None,
        trace_id: str | None,
    ) -> OrchestratorResponse:
        message = message.strip()
        if not message or len(message) > MAX_MESSAGE_CHARS or "\x00" in message:
            raise OrchestratorError("invalid_request", "message must be 1-4000 characters", 422)

        try:  # kill switch FIRST: nothing is claimed, replayed or classified while it is on
            await self._gov.require_execution_allowed()
        except GovernanceError as exc:
            await self._gov.audit_blocked(user_id, "orchestrator", "blocked", type(exc).__name__)
            _, _, code, http, msg = _classify_error(exc)
            raise OrchestratorError(code, msg, http) from None

        key_hash, req_hash = self.store.hashes(user_id, message, idem_key)
        claim = await self.store.claim(user_id, origin, key_hash, req_hash)
        if claim.replay is not None:
            stored = self.store.open_response(claim.replay)
            if stored is not None:
                return stored.model_copy(update={"replayed": True})
            return OrchestratorResponse(
                run_id=claim.replay.id,
                execution_id=claim.replay.execution_id,
                status="completed",
                intent=claim.replay.intent,
                agent_id=claim.replay.agent_id,
                workflow=claim.replay.workflow,
                replayed=True,
                reply_expired=True,
            )

        run_id = claim.run_id
        scope = RunScope(user_id=user_id, run_id=run_id)
        state: dict[str, Any] = {}
        status, reason, err = "succeeded", None, None
        async with self._f() as session:
            scope.session = session
            deps = Deps(
                classifier=self._classifier,
                router=self._router,
                governance=self._gov,
                gateway=ModelGateway(session, self._gov, self._adapters),
                memory_service=(
                    self._memory.service(session, self._gov, thread_days=self._thread_days)
                    if self._memory
                    else None
                ),
                task_type="orchestrator.request",
            )
            scope.gateway = deps.gateway
            try:
                async with asyncio.timeout(HARD_TIMEOUT_SECONDS):
                    state = await build_graph(scope, deps).ainvoke(
                        {"message": message, "trace": trace_id or ""},
                        config={"recursion_limit": RECURSION_LIMIT},
                    )
                if state.get("status") == "failed":
                    code = str(state.get("error_code") or "failed")
                    status, reason = "failed", code
                    err = ("failed", code, 502, "the model did not produce a usable answer")
                else:
                    await session.commit()
            except Exception as exc:  # noqa: BLE001 - every failure is mapped to a controlled outcome
                await session.rollback()
                status, reason, code, http, msg = _classify_error(exc)
                err = (status, code, http, msg)
                if reason == "internal_error":
                    logger.error("orchestrator internal error type=%s", type(exc).__name__)
                elif (
                    isinstance(exc, PermissionDeniedError) and scope.ctx is None
                ):  # start_run audits its own refusals
                    await self._gov.audit_blocked(
                        user_id, "orchestrator", "blocked", type(exc).__name__
                    )

        exec_id = scope.ctx.execution_id if scope.ctx is not None else None
        trace = scope.ctx.trace_id if scope.ctx is not None else None
        if scope.ctx is not None:
            await self._gov.finish(scope.ctx.execution_id, status, reason)

        intent = state.get("intent") if state else None
        agent_id = state.get("agent_id") if state else None
        workflow = state.get("workflow") if state else None
        if err is not None:
            await self.store.finish(
                run_id,
                user_id,
                status="failed",
                intent=_s(intent),
                agent_id=_s(agent_id),
                workflow=_s(workflow),
                outcome=err[0],
                error_code=err[1],
                execution_id=exec_id,
                response=None,
                store_response=False,
            )
            raise RunFailed(
                err[1], err[3], err[2], run_id=run_id, execution_id=exec_id, trace_id=trace
            )

        response = self._response(scope, state, run_id)
        await self.store.finish(
            run_id,
            user_id,
            status="completed",
            intent=response.intent,
            agent_id=response.agent_id,
            workflow=response.workflow,
            outcome=response.status,
            error_code=None,
            execution_id=exec_id,
            response=response,
            store_response=key_hash is not None,
        )
        return response

    @staticmethod
    def _response(
        scope: RunScope, state: dict[str, Any], run_id: uuid.UUID
    ) -> OrchestratorResponse:
        ctx = scope.ctx
        status = str(state["status"])
        question = state.get("question")
        return OrchestratorResponse(
            run_id=run_id,
            execution_id=ctx.execution_id,
            trace_id=ctx.trace_id,
            status=status,
            intent=_s(state.get("intent")),
            confidence=float(state.get("confidence") or 0),
            agent_id=_s(state.get("agent_id")),
            workflow=_s(state.get("workflow")),
            reply=_s(state.get("reply")),
            clarification_question=_s(question) if status == "clarification_needed" else None,
            context=ContextInfo(
                items=int(state.get("ctx_items") or 0),
                rules=int(state.get("ctx_rules") or 0),
                basis=_s(state.get("ctx_basis")),
            ),
            memory_proposals=list(state.get("proposals") or []),
            usage=Usage(
                steps=ctx.steps_used,
                input_tokens=ctx.input_tokens_used,
                output_tokens=ctx.output_tokens_used,
                cost_inr=str(ctx.cost_inr_used),
                attempts=int(state.get("attempt") or 0),
            ),
            warnings=list(state.get("warnings") or []),
        )


def _s(v: object) -> str | None:
    return None if v is None else str(v)


__all__ = ["MAX_MESSAGE_CHARS", "Orchestrator", "ScheduledTask", "time"]
