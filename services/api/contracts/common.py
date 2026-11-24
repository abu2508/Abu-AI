"""Shared building blocks. The wire is JSON: money, dates and ids are STRINGS, so they are typed as strings here."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, StringConstraints

# Exact decimal string produced by `domain.common.jsonable` (never a float).
Money = Annotated[
    str,
    StringConstraints(pattern=r"^-?\d+(\.\d+)?$"),
]
IsoDate = Annotated[str, StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}$")]
# `datetime.isoformat()`: UTC is spelled "+00:00", the user's zone as its offset (for example "+05:30").
IsoDateTime = Annotated[
    str,
    StringConstraints(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?([+-]\d{2}:\d{2}|Z)$"),
]
UuidStr = Annotated[
    str,
    StringConstraints(
        pattern=r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
    ),
]


class Contract(BaseModel):
    """Strict by design: an unexpected field or a wrong type fails the conformance tests (the drift alarm)."""

    model_config = ConfigDict(extra="forbid", strict=True, populate_by_name=True)
