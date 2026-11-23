"""Strict validation of model output. Anything that is not exactly the contract is MALFORMED."""

import json
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from core.memory.privacy import TextRejectedError, normalize_text
from core.memory.types import MemoryType, Sensitivity
from core.model_router import contract

MAX_RAW_BYTES = 16 * 1024


class MalformedOutputError(Exception):
    """The model did not honour the contract. `code` is a machine code; no model text is included."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ProposalIn(BaseModel):
    """A memory the MODEL proposes. It cannot set provenance, retention, embedding text or links."""

    model_config = ConfigDict(extra="forbid")
    namespace: str = Field(max_length=128)
    memory_type: MemoryType
    text: str = Field(min_length=1, max_length=1000)
    key: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_.:-]{0,127}$")
    sensitivity: Sensitivity = "personal"
    confidence: float = Field(default=0.5, ge=0, le=1)


class AgentOutput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reply: str = Field(min_length=1, max_length=4000)
    needs_clarification: bool = False
    clarification_question: str | None = Field(default=None, max_length=300)
    memory_proposals: list[ProposalIn] = Field(default_factory=list, max_length=10)


def parse_output(raw: str) -> AgentOutput:
    if len(raw.encode()) > MAX_RAW_BYTES:
        raise MalformedOutputError("output_too_large")
    try:
        obj: Any = json.loads(raw)
    except ValueError:
        raise MalformedOutputError("not_json") from None
    if not isinstance(obj, dict):
        raise MalformedOutputError("not_an_object")
    try:
        out = AgentOutput.model_validate(obj)
        reply = normalize_text(out.reply, max_len=4000)
        question = (
            normalize_text(out.clarification_question, max_len=300)
            if out.clarification_question
            else None
        )
    except ValidationError:
        raise MalformedOutputError("schema_violation") from None
    except TextRejectedError:
        raise MalformedOutputError("bad_text") from None
    # A model that parrots our hidden instructions is leaking them: reject rather than relay.
    if contract.MARKER in reply or "Role:" in reply and "Workflow:" in reply:
        raise MalformedOutputError("prompt_leak")
    return out.model_copy(update={"reply": reply, "clarification_question": question})
