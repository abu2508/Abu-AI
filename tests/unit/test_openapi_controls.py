"""B2: OpenAPI is off by default, never public, refused in production, and exported offline."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError

from services.api import openapi_export
from services.api.config import Settings
from services.api.main import create_app
from tests.integration.helpers import HASH, JWT, make_settings

SNAPSHOT = Path(__file__).resolve().parents[2] / openapi_export.SNAPSHOT_PATH
SCHEMA_PATHS = (
    "/openapi.json",
    "/docs",
    "/redoc",
    "/docs/oauth2-redirect",
    "/v1/dev/openapi.json",
    "/v1/dev/docs",
    "/openapi.yaml",
)


@pytest.mark.parametrize("env", ["development", "test", "production"])
def test_nothing_serves_the_schema_by_default_in_any_environment(env: str) -> None:
    with TestClient(create_app(make_settings(env=env))) as c:
        for path in SCHEMA_PATHS:
            assert c.get(path).status_code == 404, (env, path)


def test_default_mode_is_off() -> None:
    assert Settings(jwt_secret=JWT, token_hash_key=HASH).openapi_mode == "off"


def test_production_refuses_to_start_with_openapi_enabled() -> None:
    with pytest.raises(ValidationError, match="OPENAPI_MODE"):
        Settings(
            env="production", openapi_mode="authenticated", jwt_secret=JWT, token_hash_key=HASH
        )
    with pytest.raises(ValidationError):
        make_settings(env="production", openapi_mode="authenticated")
    for env in ("development", "test"):  # allowed outside production
        assert (
            Settings(
                env=env, openapi_mode="authenticated", jwt_secret=JWT, token_hash_key=HASH
            ).openapi_mode
            == "authenticated"
        )


def test_invalid_mode_value_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(openapi_mode="public", jwt_secret=JWT, token_hash_key=HASH)


def test_authenticated_route_requires_a_bearer_token() -> None:
    with TestClient(create_app(make_settings(env="test", openapi_mode="authenticated"))) as c:
        assert c.get("/v1/dev/openapi.json").status_code == 401
        assert (
            c.get("/v1/dev/openapi.json", headers={"Authorization": "Bearer garbage"}).status_code
            == 401
        )
        assert (
            c.get("/openapi.json").status_code == 404 and c.get("/docs").status_code == 404
        )  # still no public schema


def test_snapshot_is_current() -> None:
    """Fails when the backend API changed but the committed snapshot was not regenerated:
    `python -m services.admin openapi export`."""
    assert SNAPSHOT.exists(), "run: python -m services.admin openapi export"
    assert SNAPSHOT.read_text(encoding="utf-8") == openapi_export.render()


def test_export_is_deterministic_offline_and_secret_free() -> None:
    a, b = openapi_export.render(), openapi_export.render()
    assert a == b and a.endswith("\n")
    schema = json.loads(a)
    assert (
        "/v1/dev/openapi.json" not in schema["paths"]
    )  # the dev route is never part of the snapshot
    for secret in (JWT, HASH, SecretStr("export-only-")):
        assert secret.get_secret_value() not in a
    for needle in ("password", "BEGIN PRIVATE", "Bearer ey", "postgresql://", "ABU_"):
        assert needle.lower() not in a.lower() or needle == "password", needle
    assert len(schema["paths"]) >= 80 and schema["info"]["title"] == "Abu AI"


def test_public_routes_are_unchanged_by_the_dev_route() -> None:
    with TestClient(create_app(make_settings(env="test", openapi_mode="authenticated"))) as c:
        paths = set(c.app.openapi()["paths"])  # type: ignore[attr-defined]
    assert "/v1/dev/openapi.json" in paths  # present only when explicitly enabled
