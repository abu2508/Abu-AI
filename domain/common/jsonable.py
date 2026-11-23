"""JSON-safe conversion that never turns money into a float."""

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from typing import Any


def jsonable(v: Any) -> Any:
    """JSON-safe copy. Money (Decimal) becomes an exact STRING - never a float - so no digit is ever lost."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date | time):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set | frozenset):
        return [jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return jsonable(vars(v))
    return v
