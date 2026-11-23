"""/v1/auth/*. Only /enroll and /refresh are public (they present their own credential)."""

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from services.api.auth import service
from services.api.auth.deps import PrincipalDep, SessionDep, commit_or_translate
from services.api.deps import SettingsDep

router = APIRouter(prefix="/v1", tags=["auth"])


class EnrollRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enrollment_code: str = Field(min_length=8, max_length=128)
    device_name: str = Field(min_length=1, max_length=128)
    platform: str = Field(default="ios", pattern=r"^[a-z0-9_-]{2,32}$")


class RefreshRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    refresh_token: str = Field(min_length=8, max_length=256)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "Bearer"  # noqa: S105
    expires_in: int
    refresh_token: str
    session_id: uuid.UUID


class SessionInfo(BaseModel):
    id: uuid.UUID
    device_name: str
    platform: str
    created_at: datetime
    last_used_at: datetime | None
    expires_at: datetime
    revoked_at: datetime | None
    current: bool


def _pair(p: service.TokenPair) -> TokenResponse:
    return TokenResponse(
        access_token=p.access_token,
        expires_in=p.expires_in,
        refresh_token=p.refresh_token,
        session_id=p.session_id,
    )


@router.post("/auth/enroll", response_model=TokenResponse)
async def enroll(body: EnrollRequest, session: SessionDep, settings: SettingsDep) -> TokenResponse:
    try:
        pair = await service.enroll(
            session, settings, body.enrollment_code, body.device_name, body.platform
        )
        await session.commit()
    except service.AuthError as exc:
        raise await commit_or_translate(session, exc) from None
    return _pair(pair)


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest, session: SessionDep, settings: SettingsDep
) -> TokenResponse:
    try:
        pair = await service.refresh(session, settings, body.refresh_token)
        await session.commit()
    except service.AuthError as exc:
        raise await commit_or_translate(session, exc) from None
    return _pair(pair)


@router.post("/auth/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(principal: PrincipalDep, session: SessionDep) -> Response:
    await service.revoke_user_session(session, principal, principal.session_id, "logout")
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/auth/sessions", response_model=list[SessionInfo])
async def list_sessions(principal: PrincipalDep, session: SessionDep) -> list[SessionInfo]:
    rows = await service.list_sessions(session, principal.user_id)
    return [
        SessionInfo(
            id=r.id,
            device_name=r.device_name,
            platform=r.platform,
            created_at=r.created_at,
            last_used_at=r.last_used_at,
            expires_at=r.expires_at,
            revoked_at=r.revoked_at,
            current=r.id == principal.session_id,
        )
        for r in rows
    ]


@router.delete("/auth/sessions/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def revoke(session_id: uuid.UUID, principal: PrincipalDep, session: SessionDep) -> Response:
    try:
        await service.revoke_user_session(session, principal, session_id)
    except service.NotFoundError:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "session not found") from None
    await session.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me")
async def me(principal: PrincipalDep) -> dict[str, str]:
    return {"user_id": str(principal.user_id), "session_id": str(principal.session_id)}
