"""Structured domain memory: typed records validated against a registered schema, looked up EXACTLY
by (namespace, key) with SQL, and NEVER embedded. (Bank/transaction tables arrive with the finance
agent; this is the generic, validated key-value layer beside them.)"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError


class StructuredValidationError(ValueError):
    pass


@dataclass(frozen=True)
class StructuredSchema:
    name: str
    model: type[BaseModel]
    # Consequential schemas (money, deadlines, ...) always need user confirmation to change.
    consequential: bool = False


class Preference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str | int | float | bool | list[str]


class StructuredRegistry:
    def __init__(self) -> None:
        self._schemas: dict[str, StructuredSchema] = {}

    def register(self, schema: StructuredSchema) -> None:
        if schema.model.model_config.get("extra") != "forbid":
            raise ValueError("structured schemas must set extra='forbid'")
        if schema.name in self._schemas:
            raise ValueError(f"schema {schema.name!r} already registered")
        self._schemas[schema.name] = schema

    def get(self, name: str) -> StructuredSchema:
        try:
            return self._schemas[name]
        except KeyError:
            raise StructuredValidationError(f"unknown structured schema {name!r}") from None

    def validate(self, name: str, data: dict[str, Any]) -> dict[str, Any]:
        schema = self.get(name)
        try:
            return schema.model.model_validate(data).model_dump(mode="json")
        except ValidationError as exc:
            raise StructuredValidationError(
                f"data does not match schema {name!r} ({exc.error_count()} errors)"
            ) from None


def default_registry() -> StructuredRegistry:
    reg = StructuredRegistry()
    reg.register(StructuredSchema("preference", Preference))
    return reg
