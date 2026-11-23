"""FastAPI dependencies: DB session and the authenticated Principal."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from services.api.auth import service, tokens
from services.api.config import Settings

_bearer = HTTPBearer(auto_error=False)
_UNAUTHORIZED = HTTPException(
    status.HTTP_401_UNAUTHORIZED, "invalid credentials", headers={"WWW-Authenticate": "Bearer"}
)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    factory = request.app.state.sessionmaker
    if factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service unavailable")
    async with factory() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]


async def commit_or_translate(session: AsyncSession, exc: service.AuthError) -> HTTPException:
    """Persist audit/revocation writes made before a failed auth, then return a generic 401."""
    if exc.persist:
        await session.commit()
    else:
        await session.rollback()
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, "invalid credentials", headers={"WWW-Authenticate": "Bearer"}
    )


async def get_principal(
    request: Request,
    creds: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer)],
) -> service.Principal:
    """Authenticate BEFORE touching the database: a missing/garbled token is a plain 401.
    Uses its own short session so verification never shares a transaction with the handler."""
    if creds is None or creds.scheme.lower() != "bearer":
        raise _UNAUTHORIZED
    settings: Settings = request.app.state.settings
    try:  # stateless checks first: bad signature/expiry/claims never reach the database
        tokens.decode_access_token(settings, creds.credentials)
    except tokens.InvalidTokenError:
        raise _UNAUTHORIZED from None
    factory = request.app.state.sessionmaker
    if factory is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "service unavailable")
    async with factory() as auth_session:
        try:
            principal = await service.authenticate(auth_session, settings, creds.credentials)
            await auth_session.commit()  # persists the throttled last_used_at
        except service.AuthError:
            raise _UNAUTHORIZED from None
    wait = request.app.state.limiter.check(
        f"user:{principal.user_id}", settings.rate_limit_user_per_minute
    )
    if wait is not None:
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            "rate limit exceeded",
            headers={"Retry-After": str(int(wait) + 1)},
        )
    return principal


PrincipalDep = Annotated[service.Principal, Depends(get_principal)]
