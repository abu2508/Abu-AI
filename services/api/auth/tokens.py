"""Token primitives. Access tokens are short-lived HS256 JWTs; refresh tokens and enrollment
codes are opaque random secrets stored only as keyed HMAC-SHA256 hashes."""

import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from pydantic import SecretStr

from services.api.config import Settings

ALGORITHM = "HS256"
_REQUIRED = ["exp", "iat", "sub", "sid", "jti", "iss", "aud", "typ"]


class InvalidTokenError(Exception):
    pass


def new_secret(prefix: str, nbytes: int = 32) -> str:
    """256-bit (default) URL-safe random secret with a recognisable prefix."""
    return f"{prefix}_{secrets.token_urlsafe(nbytes)}"


def hash_secret(key: SecretStr, value: str) -> str:
    return hmac.new(key.get_secret_value().encode(), value.encode(), hashlib.sha256).hexdigest()


@dataclass(frozen=True)
class AccessClaims:
    user_id: uuid.UUID
    session_id: uuid.UUID
    jti: str


def issue_access_token(
    settings: Settings, user_id: uuid.UUID, session_id: uuid.UUID, now: datetime | None = None
) -> tuple[str, int]:
    secret, _ = settings.require_auth_secrets()
    now = now or datetime.now(UTC)
    ttl = settings.access_token_ttl_seconds
    payload = {
        "iss": settings.jwt_issuer,
        "aud": settings.jwt_audience,
        "sub": str(user_id),
        "sid": str(session_id),
        "jti": uuid.uuid4().hex,
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=ttl)).timestamp()),
    }
    return jwt.encode(payload, secret.get_secret_value(), algorithm=ALGORITHM), ttl


def decode_access_token(settings: Settings, token: str) -> AccessClaims:
    secret, _ = settings.require_auth_secrets()
    try:
        claims = jwt.decode(
            token,
            secret.get_secret_value(),
            algorithms=[ALGORITHM],  # pinned: rejects alg=none and algorithm confusion
            audience=settings.jwt_audience,
            issuer=settings.jwt_issuer,
            options={"require": _REQUIRED},
        )
        if claims["typ"] != "access":
            raise InvalidTokenError("wrong token type")
        return AccessClaims(uuid.UUID(claims["sub"]), uuid.UUID(claims["sid"]), claims["jti"])
    except (jwt.PyJWTError, ValueError, KeyError) as exc:
        raise InvalidTokenError("invalid access token") from exc
