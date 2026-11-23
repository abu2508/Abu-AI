"""Shared column helpers."""

import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, func, text
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column


def enum_col(e: type[enum.Enum], name: str) -> SAEnum:
    """Store enums as VARCHAR + CHECK (simple to migrate, unlike native PG enums)."""
    return SAEnum(
        e,
        name=name,
        native_enum=False,
        create_constraint=True,
        values_callable=lambda x: [m.name.lower() for m in x],
        length=32,
    )


def uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
        server_default=text("gen_random_uuid()"),
    )


def created_at_col() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now())
