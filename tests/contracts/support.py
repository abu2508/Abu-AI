"""Helpers shared by the contract tests."""

import json
import re
import uuid
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

GOLDEN = Path(__file__).resolve().parent / "golden" / "golden.json"
_PLACEHOLDER = re.compile(r"<uuid-(\d+)>")


def load_golden() -> dict[str, Any]:
    """The recorded synthetic responses with `<uuid-N>` placeholders turned into real (fixed) UUIDs."""
    text = GOLDEN.read_text(encoding="utf-8")
    text = _PLACEHOLDER.sub(lambda m: str(uuid.UUID(int=int(m.group(1)))), text)
    return json.loads(text)  # type: ignore[no-any-return]


def validate(model: Any, body: Any) -> Any:
    """Validate a decoded JSON body exactly as the wire would present it (JSON mode, strict models)."""
    return TypeAdapter(model).validate_json(json.dumps(body))
