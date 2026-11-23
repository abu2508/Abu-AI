"""Shared plumbing for the domain APIs (/v1/finance, /v1/messaging, /v1/coach).

These routes are the USER managing their OWN data: they stay available while the kill switch is on (it
stops agents/tools, not the owner's access to or deletion of their data), always act as the authenticated
user, and never take a user id from the client.
"""

import base64
import binascii
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Any

from fastapi import Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from domain.common.crypto import FieldCipher


def get_cipher(request: Request) -> FieldCipher:
    runtime = request.app.state.memory
    settings = request.app.state.settings
    if runtime is None or settings.token_hash_key is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service unavailable")
    return FieldCipher(runtime.envelope, settings.token_hash_key)


CipherDep = Annotated[FieldCipher, Depends(get_cipher)]


def error_response(code: str, message: str, http_status: int) -> JSONResponse:
    return JSONResponse(
        status_code=http_status, content={"error": {"code": code, "message": message}}
    )


def decode_b64(content: str, limit: int) -> bytes:
    try:
        data = base64.b64decode(content, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "content must be base64"
        ) from None
    if len(data) > limit:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "file too large")
    return data


def jsonable(v: Any) -> Any:
    """JSON-safe copy. Money (Decimal) becomes an exact STRING - never a float - so no digit is ever lost."""
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, uuid.UUID):
        return str(v)
    if isinstance(v, datetime | date):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): jsonable(x) for k, x in v.items()}
    if isinstance(v, list | tuple | set | frozenset):
        return [jsonable(x) for x in v]
    if hasattr(v, "__dataclass_fields__"):
        return jsonable(vars(v))
    return v
