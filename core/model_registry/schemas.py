"""Validated input schemas for registry operations."""

import enum
import re
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from database.schemas import Tri

_SLUG = re.compile(r"^[a-z0-9][a-z0-9._-]{1,127}$")


class Capability(enum.StrEnum):
    TEXT_GENERATION = "text_generation"
    STRUCTURED_OUTPUT = "structured_output"
    FUNCTION_CALLING = "function_calling"
    VISION = "vision"
    AUDIO_INPUT = "audio_input"
    EMBEDDINGS = "embeddings"
    LONG_CONTEXT = "long_context"


class PolicyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=2, max_length=64)
    retention_summary: str = Field(min_length=1)
    trains_on_data: Tri = Tri.UNKNOWN
    human_review: Tri = Tri.UNKNOWN
    source_url: str | None = None
    verified_at: datetime | None = None

    @field_validator("provider")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("provider must be a lowercase slug")
        return v


class PricingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_per_million: Decimal = Field(ge=0)
    output_per_million: Decimal = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source_url: str | None = None
    verified_at: datetime | None = None


class ModelIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    provider: str
    provider_model_id: str = Field(min_length=1, max_length=256)
    gateway_model_name: str = Field(min_length=1, max_length=256)
    version: str | None = None
    context_limit: int | None = Field(default=None, gt=0)
    max_output_tokens: int | None = Field(default=None, gt=0)
    rpm_limit: int | None = Field(default=None, gt=0)
    rpd_limit: int | None = Field(default=None, gt=0)
    rate_limits_verified: bool = False
    availability_verified_at: datetime | None = None
    capabilities: list[Capability] = Field(min_length=1)
    pricing: PricingIn
    notes: str | None = None

    @field_validator("name")
    @classmethod
    def _slug(cls, v: str) -> str:
        if not _SLUG.match(v):
            raise ValueError("name must be a lowercase slug")
        return v

    @field_validator("provider_model_id", "gateway_model_name")
    @classmethod
    def _no_blank(cls, v: str) -> str:
        if not v.strip() or v != v.strip():
            raise ValueError("must be non-blank without surrounding whitespace")
        return v
