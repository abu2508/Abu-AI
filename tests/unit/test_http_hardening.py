"""Whole-app security properties that need no database."""

import re
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any, cast

import httpx
import pytest
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from services.api.auth import tokens
from services.api.main import create_app
from services.api.middleware import client_ip
from services.api.ratelimit import RateLimiter
from tests.integration.helpers import make_settings

# The COMPLETE list of endpoints reachable without a bearer token. Anything else must 401.
PUBLIC = {("GET", "/health"), ("POST", "/v1/auth/enroll"), ("POST", "/v1/auth/refresh")}


@pytest.fixture
def prod_client() -> Iterator[TestClient]:
    with TestClient(create_app(make_settings(env="production"))) as c:
        yield c


def app_of(c: TestClient) -> FastAPI:
    return cast(FastAPI, c.app)


def _routes(app: FastAPI) -> Iterator[tuple[str, str]]:
    """Every operation, via the OpenAPI schema (complete across nested routers)."""
    for path, ops in app.openapi()["paths"].items():
        for method in ops:
            yield method.upper(), re.sub(r"\{[^}]+\}", "00000000-0000-0000-0000-000000000000", path)


def _walk(routes: list[Any]) -> Iterator[Any]:
    for r in routes:
        inner = getattr(r, "original_router", None)
        if inner is not None:
            yield from _walk(inner.routes)
        else:
            yield r


def test_no_route_is_hidden_from_the_schema(prod_client: TestClient) -> None:
    """The enumeration above only works if nothing opts out of the schema."""
    api_routes = [r for r in _walk(app_of(prod_client).routes) if isinstance(r, APIRoute)]
    assert len(api_routes) >= 10
    assert all(r.include_in_schema for r in api_routes)


def test_every_non_public_route_requires_authentication(prod_client: TestClient) -> None:
    seen = list(_routes(app_of(prod_client)))
    assert len(seen) >= 10  # guard against the enumeration silently finding nothing
    for method, path in seen:
        if (method, path) in PUBLIC:
            continue
        resp = prod_client.request(method, path)
        assert resp.status_code == 401, (
            f"{method} {path} returned {resp.status_code} without a token"
        )
        assert resp.headers["www-authenticate"] == "Bearer"


def test_public_routes_are_exactly_the_documented_ones(prod_client: TestClient) -> None:
    public_found = {
        (m, p)
        for m, p in _routes(app_of(prod_client))
        if prod_client.request(m, p).status_code != 401
    }
    assert public_found == PUBLIC


def good_token(**kw: object) -> str:
    import uuid

    return tokens.issue_access_token(make_settings(), uuid.uuid4(), uuid.uuid4(), **kw)[0]  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "header",
    [
        "Bearer garbage",
        "Bearer ",
        "Basic dXNlcjpwYXNz",
        "bearer",
        f"Bearer {tokens.issue_access_token(make_settings(), __import__('uuid').uuid4(), __import__('uuid').uuid4(), datetime.now(UTC) - timedelta(hours=2))[0]}",
    ],
)
def test_invalid_credentials_get_generic_401(prod_client: TestClient, header: str) -> None:
    resp = prod_client.get("/v1/me", headers={"Authorization": header})
    assert resp.status_code == 401
    assert resp.json() == {"detail": "invalid credentials"}


def test_token_signed_with_other_secret_rejected(prod_client: TestClient) -> None:
    import uuid

    other = make_settings(jwt_secret="z" * 48)
    tok, _ = tokens.issue_access_token(other, uuid.uuid4(), uuid.uuid4())
    assert prod_client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 401


def test_validly_signed_token_for_nonexistent_session_without_db_is_not_accepted(
    prod_client: TestClient,
) -> None:
    # Signature alone is never enough: the session must exist. With no database it fails closed.
    resp = prod_client.get("/v1/me", headers={"Authorization": f"Bearer {good_token()}"})
    assert resp.status_code in (401, 503)


def test_security_headers_and_no_server_banner(prod_client: TestClient) -> None:
    resp = prod_client.get("/health")
    assert resp.headers["cache-control"] == "no-store"
    assert resp.headers["x-content-type-options"] == "nosniff"
    assert "server" not in resp.headers


def test_oversized_body_rejected_by_content_length(prod_client: TestClient) -> None:
    resp = prod_client.post(
        "/v1/auth/enroll", content=b"x" * 70_000, headers={"content-type": "application/json"}
    )
    assert resp.status_code == 413


async def test_oversized_streamed_body_rejected_without_content_length() -> None:
    app = create_app(make_settings(env="production", max_request_bytes=2048))

    async def chunks() -> AsyncIterator[bytes]:
        for _ in range(10):
            yield b"x" * 1024

    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c:
        resp = await c.post(
            "/v1/auth/enroll", content=chunks(), headers={"content-type": "application/json"}
        )
    assert resp.status_code == 413


def test_body_under_limit_reaches_validation(prod_client: TestClient) -> None:
    resp = prod_client.post("/v1/auth/enroll", json={"enrollment_code": "x"})
    assert resp.status_code in (422, 503)  # reached the handler (503: no DB here); not a 413


def test_auth_endpoints_are_rate_limited_per_ip() -> None:
    app = create_app(make_settings(env="production", rate_limit_auth_per_minute=3))
    with TestClient(app) as c:
        codes = [
            c.post("/v1/auth/refresh", json={"refresh_token": "x" * 20}).status_code
            for _ in range(5)
        ]
    assert codes[:3] != [429] * 3 and codes[3:] == [429, 429]


def test_rate_limit_response_has_retry_after() -> None:
    app = create_app(make_settings(env="production", rate_limit_auth_per_minute=1))
    with TestClient(app) as c:
        c.post("/v1/auth/refresh", json={"refresh_token": "x" * 20})
        resp = c.post("/v1/auth/refresh", json={"refresh_token": "x" * 20})
    assert resp.status_code == 429 and int(resp.headers["retry-after"]) >= 1


def test_spoofed_forwarded_for_does_not_bypass_limit_by_default() -> None:
    app = create_app(make_settings(env="production", rate_limit_auth_per_minute=2))
    with TestClient(app) as c:
        codes = [
            c.post(
                "/v1/auth/refresh",
                json={"refresh_token": "x" * 20},
                headers={"x-forwarded-for": f"9.9.9.{i}"},
            ).status_code
            for i in range(4)
        ]
    assert codes[2:] == [429, 429]


def test_global_ip_limit() -> None:
    app = create_app(make_settings(env="production", rate_limit_ip_per_minute=5))
    with TestClient(app) as c:
        codes = [c.get("/health").status_code for _ in range(7)]
    assert codes[:5] == [200] * 5 and codes[5:] == [429, 429]


def test_client_ip_trusts_only_configured_proxy_hops() -> None:
    def scope(xff: str) -> dict[str, object]:
        return {"client": ("10.0.0.1", 1), "headers": [(b"x-forwarded-for", xff.encode())]}

    assert client_ip(scope("1.1.1.1, 2.2.2.2"), 0) == "10.0.0.1"
    # one trusted proxy appended the real client as the LAST entry; the left is attacker-controlled
    assert client_ip(scope("6.6.6.6, 203.0.113.9"), 1) == "203.0.113.9"
    assert client_ip({"client": ("10.0.0.1", 1), "headers": []}, 1) == "10.0.0.1"


def test_ratelimiter_window_slides() -> None:
    now = [0.0]
    rl = RateLimiter(clock=lambda: now[0])
    assert [rl.check("k", 2) for _ in range(2)] == [None, None]
    assert rl.check("k", 2) is not None
    assert rl.check("other", 2) is None
    now[0] = 61
    assert rl.check("k", 2) is None
