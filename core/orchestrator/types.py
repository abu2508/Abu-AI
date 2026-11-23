"""Orchestrator types. Everything a classifier or a model can influence is an ENUM or a validated
model: neither user text nor model output can name an agent, a model, a tool or a namespace."""

import re
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from core.model_router.types import Message

SLUG = re.compile(r"^[a-z][a-z0-9_.-]{1,63}$")


class Intent(StrEnum):
    FINANCE = "finance"
    MESSAGING = "messaging"
    COACHING = "coaching"
    GENERAL = "general"
    AMBIGUOUS = "ambiguous"  # two pillars fit: ask which
    CLARIFICATION_NEEDED = "clarification_needed"  # not enough to route
    UNSUPPORTED = "unsupported"  # asks for something we will not/cannot do


ROUTABLE = (Intent.FINANCE, Intent.MESSAGING, Intent.COACHING, Intent.GENERAL)


class Classification(BaseModel):
    """What a classifier must return. A model-based classifier is held to exactly this shape."""

    model_config = ConfigDict(extra="forbid")

    intent: Intent
    subtype: str | None = Field(default=None, pattern=r"^[a-z][a-z0-9_]{1,31}$")
    confidence: float = Field(ge=0, le=1)
    candidates: list[Intent] = Field(default_factory=list, max_length=4)
    reasons: list[str] = Field(default_factory=list, max_length=8)  # machine codes, never user text
    flags: list[str] = Field(default_factory=list, max_length=8)


class Classifier(Protocol):
    def classify(self, message: str) -> Classification: ...


class OrchestratorError(Exception):
    """A controlled failure with a stable machine code and an HTTP status. Messages never contain user
    content, prompts or memory."""

    def __init__(self, code: str, message: str, http_status: int = 500) -> None:
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass(frozen=True)
class PreparedPrompt:
    messages: list[Message]


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation PLANNED by workflow code (never by a model). `name` must be in the workflow's allowlist."""

    name: str
    args: dict[str, Any]


class WorkflowHandler(Protocol):
    """Domain extension point: a workflow supplies its prompt, its (deterministic) tool plan and its summary."""

    def build_messages(
        self,
        spec: "WorkflowSpec",
        message: str,
        context_block: str | None,
        *,
        repair: bool,
        data_block: str | None = None,
    ) -> list[Message]: ...

    def plan_tools(
        self, spec: "WorkflowSpec", message: str, conversation_id: uuid.UUID | None
    ) -> list[ToolCall]: ...

    def summarise(self, results: dict[str, Any]) -> str | None: ...


@dataclass(frozen=True)
class WorkflowSpec:
    """Everything that bounds one workflow. Declarative: the orchestrator enforces it, the workflow
    cannot widen it. (The agent VERSION's own grants and limits are enforced separately by RunContext.)"""

    name: str
    intent: Intent
    agent_id: str
    model_alias: str
    handler: WorkflowHandler
    description: str
    subtype: str | None = None  # slot for future specialised workflows under a pillar
    memory_roots: tuple[
        str, ...
    ] = ()  # namespace ROOTS this workflow may read (always intersected with grants)
    write_roots: tuple[str, ...] = ()  # roots where model-proposed memories may be filed
    tool_allowlist: tuple[
        str, ...
    ] = ()  # EMPTY: the orchestrator has no tool access in this milestone
    max_graph_steps: int = 24
    timeout_seconds: int = 30
    max_retries: int = 1
    max_proposals: int = 3
    context_budget_tokens: int = 800
    mock: bool = True


@dataclass(frozen=True)
class RouteDecision:
    kind: Literal["workflow", "clarify", "unsupported"]
    agent_id: (
        str  # the registered identity that executes/answers (front-desk for clarify/unsupported)
    )
    spec: WorkflowSpec | None
    message: str | None = None  # static text for clarify/unsupported


class MemoryProposalOut(BaseModel):
    id: uuid.UUID
    status: str
    needs_confirmation: bool


class ContextInfo(BaseModel):
    """Counts only. Memory TEXT is never part of a response."""

    items: int = 0
    rules: int = 0
    basis: str | None = None
    note: str | None = None


class Usage(BaseModel):
    steps: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_inr: str = "0"
    attempts: int = 0


class OrchestratorResponse(BaseModel):
    run_id: uuid.UUID
    execution_id: uuid.UUID | None = None
    trace_id: str | None = None
    status: Literal["completed", "clarification_needed", "unsupported", "failed"]
    intent: str | None = None
    confidence: float | None = None
    agent_id: str | None = None
    workflow: str | None = None
    mock: bool = True  # every workflow in this milestone is a placeholder: never pretend otherwise
    reply: str | None = None
    clarification_question: str | None = None
    context: ContextInfo = Field(default_factory=ContextInfo)
    memory_proposals: list[MemoryProposalOut] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    data: dict[str, Any] | None = (
        None  # deterministic results from the user's own records (money as strings)
    )
    summary: str | None = (
        None  # templated text built from `data`: no model wording, no invented figures
    )
    warnings: list[str] = Field(default_factory=list)
    error: dict[str, str] | None = None
    replayed: bool = False
    reply_expired: bool = False


NodeFn = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class RunScope:
    """Per-run, non-serialisable objects. Kept OUT of graph state so the graph stays checkpoint-able later."""

    user_id: uuid.UUID
    run_id: uuid.UUID
    ctx: Any = None
    gateway: Any = None
    memory: Any = None
    session: Any = None
    spec: Any = None
    classification: Any = None
    graph_steps: int = 0
    started: float = field(default_factory=time.monotonic)
    raw_output: str | None = None
    output: Any = None
    step_limit: int = 24
    conversation_id: uuid.UUID | None = None
    tool_results: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


class RunFailed(OrchestratorError):
    """A governed run that ended without a usable answer. Carries ids so the caller can point at the record."""

    def __init__(
        self,
        code: str,
        message: str,
        http_status: int,
        *,
        run_id: uuid.UUID,
        execution_id: uuid.UUID | None,
        trace_id: str | None,
    ) -> None:
        super().__init__(code, message, http_status)
        self.run_id, self.execution_id, self.trace_id = run_id, execution_id, trace_id
