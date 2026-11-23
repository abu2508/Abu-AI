import uuid
from datetime import UTC, datetime, timedelta

import jwt
import pytest
from pydantic import SecretStr

from services.api.auth import tokens
from tests.integration.helpers import JWT, make_settings

SETTINGS = make_settings()
UID, SID = uuid.uuid4(), uuid.uuid4()


def good_claims(**over: object) -> dict[str, object]:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "iss": SETTINGS.jwt_issuer,
        "aud": SETTINGS.jwt_audience,
        "sub": str(UID),
        "sid": str(SID),
        "jti": "j",
        "typ": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(minutes=5)).timestamp()),
    }
    claims.update(over)
    return claims


def sign(claims: dict[str, object], key: str | None = None, alg: str = "HS256") -> str:
    return jwt.encode(claims, key or JWT.get_secret_value(), algorithm=alg)


def test_roundtrip() -> None:
    token, ttl = tokens.issue_access_token(SETTINGS, UID, SID)
    assert ttl == SETTINGS.access_token_ttl_seconds <= 3600
    claims = tokens.decode_access_token(SETTINGS, token)
    assert (claims.user_id, claims.session_id) == (UID, SID)


@pytest.mark.parametrize(
    "token",
    [
        "garbage",
        "",
        sign(
            good_claims(exp=int((datetime.now(UTC) - timedelta(seconds=5)).timestamp()))
        ),  # expired
        sign(good_claims(), key="k" * 48),  # wrong secret
        sign(good_claims(aud="someone-else")),
        sign(good_claims(iss="evil")),
        sign(good_claims(typ="refresh")),
        sign({k: v for k, v in good_claims().items() if k != "sid"}),  # missing claim
        sign({k: v for k, v in good_claims().items() if k != "exp"}),  # no expiry
        sign(good_claims(sub="not-a-uuid")),
        jwt.encode(good_claims(), key=None, algorithm="none"),  # alg=none
        sign(good_claims(), alg="HS512"),  # algorithm other than the pinned one
    ],
)
def test_invalid_tokens_rejected(token: str) -> None:
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access_token(SETTINGS, token)


def test_tampered_payload_rejected() -> None:
    token, _ = tokens.issue_access_token(SETTINGS, UID, SID)
    head, payload, sig = token.split(".")
    forged = jwt.utils.base64url_encode(b'{"sub":"' + str(uuid.uuid4()).encode() + b'"}').decode()
    with pytest.raises(tokens.InvalidTokenError):
        tokens.decode_access_token(SETTINGS, f"{head}.{forged}.{sig}")


def test_secrets_are_random_prefixed_and_hash_is_keyed() -> None:
    a, b = tokens.new_secret("rt"), tokens.new_secret("rt")
    assert a != b and a.startswith("rt_") and len(a) >= 40
    k1, k2 = SecretStr("a" * 40), SecretStr("b" * 40)
    assert tokens.hash_secret(k1, a) == tokens.hash_secret(k1, a)
    assert tokens.hash_secret(k1, a) != tokens.hash_secret(k2, a)
    assert a not in tokens.hash_secret(k1, a) and len(tokens.hash_secret(k1, a)) == 64
