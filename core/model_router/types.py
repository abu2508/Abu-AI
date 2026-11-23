from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from database.schemas import DataClass


class Message(BaseModel):
    model_config = ConfigDict(extra="forbid")
    role: Literal["system", "user", "assistant"]
    content: str = Field(min_length=1, max_length=200_000)


class CompletionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    messages: list[Message] = Field(min_length=1, max_length=100)
    max_tokens: int = Field(default=256, ge=1, le=8192)
    # No default on purpose: every caller must declare how sensitive the payload is.
    data_class: DataClass


class CompletionResult(BaseModel):
    text: str
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    model_name: str
    provider: str
    latency_ms: float
    # False when the provider did not report token usage: the cost is then UNKNOWN (fail closed).
    usage_reported: bool = True
    # Cost as reported by the gateway (LiteLLM) in USD, if any. Used as the confirmed cost; never
    # added on top of the application ledger (one ledger row per call => no double counting).
    provider_cost_usd: Decimal | None = None
