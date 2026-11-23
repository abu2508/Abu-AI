"""The orchestrator's LangGraph workflow (one run = one bounded, in-memory graph execution).

    classify -> select_agent -> govern -> [assemble_context -> execute <-> validate -> remember] -> respond
                                   \\__ clarify / unsupported ________________________________/
    any handled failure -> fail

* Compiled WITHOUT a checkpointer: nothing about the conversation is saved automatically.
* State holds only plain data. Live objects (DB session, RunContext, gateway, memory handle) live in the
  per-run RunScope, so the graph can be checkpointed later without serialising secrets or capabilities.
* Bounded three ways: every node counts against `max_graph_steps`, each node runs under the remaining
  workflow time, and LangGraph's own `recursion_limit` is a hard backstop. Retries are bounded by the spec.
* The graph has NO tool node and cannot call tools; models are reached only through ModelGateway.
"""

import asyncio
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from core.audit.service import Actor, record_event_durable
from core.governance.service import Governance
from core.memory.agent import AgentMemory
from core.memory.namespaces import NamespaceError
from core.memory.privacy import TextRejectedError
from core.memory.service import MemoryService
from core.memory.types import MemoryProposal, PolicyViolationError
from core.model_router.adapters import ProviderError
from core.model_router.gateway import ModelGateway
from core.model_router.types import CompletionRequest
from core.orchestrator.output import AgentOutput, MalformedOutputError, parse_output
from core.orchestrator.types import (
    Classification,
    Classifier,
    MemoryProposalOut,
    RunScope,
)
from core.orchestrator.workflows import Router
from core.permissions.errors import LimitExceededError, PermissionDeniedError
from core.permissions.runtime import start_run
from database.schemas import DataClass

RECURSION_LIMIT = 60  # absolute backstop, above every WorkflowRegistry.MAX_STEPS
DEFAULT_TIMEOUT = 30.0  # before a workflow (and so its own timeout) is known
MAX_REPLY_TOKENS = 512


class State(TypedDict, total=False):
    message: str
    intent: str
    confidence: float
    flags: list[str]
    kind: str
    agent_id: str
    workflow: str | None
    static_text: str | None
    context_block: str | None
    ctx_items: int
    ctx_rules: int
    ctx_basis: str | None
    attempt: int
    retry: bool
    repair: bool
    error_code: str | None
    reply: str | None
    question: str | None
    needs_clarification: bool
    proposals: list[dict[str, Any]]
    warnings: list[str]
    status: str


@dataclass
class Deps:
    classifier: Classifier
    router: Router
    governance: Governance
    gateway: ModelGateway
    memory_service: MemoryService | None
    task_type: str


def _timed(
    scope: RunScope, name: str, fn: Callable[[State], Awaitable[dict[str, Any]]]
) -> Callable[[State], Awaitable[dict[str, Any]]]:
    """Every node: counts as a graph step, and runs under the remaining workflow time."""

    async def node(state: State) -> dict[str, Any]:
        scope.graph_steps += 1
        if scope.graph_steps > scope.step_limit:
            raise LimitExceededError("maximum graph steps reached")
        limit = float(scope.spec.timeout_seconds) if scope.spec is not None else DEFAULT_TIMEOUT
        remaining = limit - (time.monotonic() - scope.started)
        if remaining <= 0:
            raise LimitExceededError("workflow timeout exceeded")
        try:
            async with asyncio.timeout(remaining):
                return await fn(state)
        except TimeoutError:
            raise LimitExceededError("workflow timeout exceeded") from None

    node.__name__ = name
    return node


def build_graph(scope: RunScope, deps: Deps) -> Any:
    g: StateGraph[State] = StateGraph(State)

    async def classify(state: State) -> dict[str, Any]:
        c: Classification = deps.classifier.classify(state["message"])
        scope.classification = c
        return {"intent": c.intent.value, "confidence": c.confidence, "flags": c.flags}

    async def select_agent(state: State) -> dict[str, Any]:
        d = deps.router.select(scope.classification)
        scope.spec = d.spec
        if d.spec is not None:
            scope.step_limit = d.spec.max_graph_steps
        return {
            "kind": d.kind,
            "agent_id": d.agent_id,
            "workflow": d.spec.name if d.spec else None,
            "static_text": d.message,
        }

    async def govern(state: State) -> dict[str, Any]:
        """Kill switch, agent registry (enabled + active version), permission engine (model alias)."""
        scope.ctx = await start_run(
            scope.session,
            user_id=scope.user_id,
            agent_id=state["agent_id"],
            governance=deps.governance,
            task_type=deps.task_type,
        )
        if scope.spec is not None:
            if scope.spec.tool_allowlist:  # defence in depth: registry already refuses this
                raise PermissionDeniedError("tools are not available to the orchestrator")
            scope.ctx.authorize_alias(scope.spec.model_alias)
        await record_event_durable(
            deps.governance.factory,
            Actor.user(scope.user_id),
            "orchestrator.routed",
            "execution",
            str(scope.ctx.execution_id),
            {
                "intent": state["intent"],
                "kind": state["kind"],
                "agent_id": state["agent_id"],
                "workflow": state.get("workflow"),
                "flags": state.get("flags", []),
            },
        )
        return {}

    async def assemble_context(state: State) -> dict[str, Any]:
        spec, ctx = scope.spec, scope.ctx
        if deps.memory_service is None:
            return {"warnings": [*state.get("warnings", []), "memory_unavailable"]}
        scope.memory = AgentMemory(deps.memory_service, ctx)
        granted: list[str] = []
        for root in spec.memory_roots:
            try:
                ctx.authorize_memory(root)  # own namespace, or "user" ONLY if explicitly granted
                granted.append(root)
            except PermissionDeniedError:
                continue
        if not granted:
            return {"context_block": None, "ctx_items": 0, "ctx_rules": 0}
        bundle, rendered = await scope.memory.context(
            state["message"], granted, budget_tokens=spec.context_budget_tokens
        )
        has = bool(bundle.items or bundle.rules)
        return {
            "context_block": rendered if has else None,
            "ctx_items": len(bundle.items),
            "ctx_rules": len(bundle.rules),
            "ctx_basis": bundle.similarity_basis,
        }

    async def execute(state: State) -> dict[str, Any]:
        spec, ctx = scope.spec, scope.ctx
        attempt = state.get("attempt", 0) + 1
        messages = spec.handler.build_messages(
            spec, state["message"], state.get("context_block"), repair=state.get("repair", False)
        )
        request = CompletionRequest(
            messages=messages,
            max_tokens=max(1, min(MAX_REPLY_TOKENS, ctx.remaining_output_tokens)),
            data_class=DataClass.SENSITIVE,  # user text is treated as sensitive: only models cleared for it are eligible
        )
        try:
            result = await scope.gateway.complete(ctx=ctx, alias=spec.model_alias, request=request)
        except ProviderError:
            if attempt <= spec.max_retries:
                return {"attempt": attempt, "retry": True}
            return {"attempt": attempt, "retry": False, "error_code": "provider_error"}
        scope.raw_output = result.text
        return {"attempt": attempt, "retry": False}

    async def validate(state: State) -> dict[str, Any]:
        spec = scope.spec
        try:
            out: AgentOutput = parse_output(scope.raw_output or "")
        except MalformedOutputError:
            if state.get("attempt", 1) <= spec.max_retries:
                return {"retry": True, "repair": True}
            return {"retry": False, "error_code": "malformed_output"}
        scope.output = out
        return {
            "retry": False,
            "reply": out.reply,
            "needs_clarification": out.needs_clarification,
            "question": out.clarification_question,
        }

    async def remember(state: State) -> dict[str, Any]:
        """Model-proposed memories are only PROPOSALS: forced source, restricted namespaces, capped, approval-gated."""
        spec, ctx, out = scope.spec, scope.ctx, scope.output
        done: list[dict[str, Any]] = []
        warnings = list(state.get("warnings", []))
        if out is None or not out.memory_proposals:
            return {"proposals": done}
        if scope.memory is None or spec.max_proposals == 0:
            return {"proposals": done, "warnings": [*warnings, "memory_proposals_ignored"]}
        for p in out.memory_proposals[: spec.max_proposals]:
            root = p.namespace.split("/", 1)[0]
            if root not in spec.write_roots or p.memory_type == "structured":
                warnings.append("memory_proposal_rejected")
                continue
            try:
                ctx.authorize_memory(p.namespace, write=True)
                res = await scope.memory.propose(
                    MemoryProposal(
                        namespace=p.namespace,
                        memory_type=p.memory_type,
                        text=p.text,
                        key=p.key,
                        sensitivity=p.sensitivity,
                        confidence=p.confidence,
                        source_kind="agent_inference",
                    )
                )
                done.append(
                    MemoryProposalOut(
                        id=res.id, status=res.status, needs_confirmation=res.needs_confirmation
                    ).model_dump(mode="json")
                )
            except (PolicyViolationError, PermissionDeniedError, TextRejectedError, NamespaceError):
                warnings.append("memory_proposal_rejected")
        if len(out.memory_proposals) > spec.max_proposals:
            warnings.append("memory_proposals_truncated")
        return {"proposals": done, "warnings": warnings}

    async def respond(state: State) -> dict[str, Any]:
        kind = state["kind"]
        if kind == "clarify":
            return {
                "status": "clarification_needed",
                "reply": None,
                "question": state.get("static_text"),
            }
        if kind == "unsupported":
            return {"status": "unsupported", "reply": state.get("static_text")}
        if state.get("needs_clarification"):
            return {
                "status": "clarification_needed",
                "question": state.get("question") or state.get("reply"),
            }
        return {"status": "completed"}

    async def fail(state: State) -> dict[str, Any]:
        return {"status": "failed"}

    for name, fn in (
        ("classify", classify),
        ("select_agent", select_agent),
        ("govern", govern),
        ("assemble_context", assemble_context),
        ("execute", execute),
        ("validate", validate),
        ("remember", remember),
        ("respond", respond),
        ("fail", fail),
    ):
        g.add_node(name, _timed(scope, name, fn))  # type: ignore[call-overload]

    g.add_edge(START, "classify")
    g.add_edge("classify", "select_agent")
    g.add_edge("select_agent", "govern")
    g.add_conditional_edges(
        "govern",
        lambda s: "assemble_context" if s["kind"] == "workflow" else "respond",
        {"assemble_context": "assemble_context", "respond": "respond"},
    )
    g.add_edge("assemble_context", "execute")
    g.add_conditional_edges(
        "execute",
        lambda s: "fail" if s.get("error_code") else ("execute" if s.get("retry") else "validate"),
        {"fail": "fail", "execute": "execute", "validate": "validate"},
    )
    g.add_conditional_edges(
        "validate",
        lambda s: "fail" if s.get("error_code") else ("execute" if s.get("retry") else "remember"),
        {"fail": "fail", "execute": "execute", "remember": "remember"},
    )
    g.add_edge("remember", "respond")
    g.add_edge("respond", END)
    g.add_edge("fail", END)
    return g.compile()
