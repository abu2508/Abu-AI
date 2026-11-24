"""Development-only schema route. Registered ONLY when ABU_OPENAPI_MODE=authenticated (never in production: the
settings validator refuses that combination at startup). Requires the normal bearer authentication, like every
other non-public route, so a forwarded port does not make the API surface public."""

from typing import Any

from fastapi import APIRouter, Request

from services.api.auth.deps import PrincipalDep

router = APIRouter(prefix="/v1/dev", tags=["dev"])


@router.get("/openapi.json")
async def openapi_schema(principal: PrincipalDep, request: Request) -> dict[str, Any]:
    del principal  # authentication is the whole check; the schema holds no per-user data
    schema: dict[str, Any] = request.app.openapi()
    return schema
