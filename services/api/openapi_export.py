"""Offline OpenAPI export: builds the application object in-process (no server, no database, no network, no
credentials) and renders its schema deterministically. The committed snapshot is the input for the mobile app's
type generation, and a test fails when it is stale."""

import json
from typing import Any

from pydantic import SecretStr

from services.api.config import Settings
from services.api.main import create_app

SNAPSHOT_PATH = "mobile/openapi/openapi.json"


def build_schema() -> dict[str, Any]:
    # Throw-away, obviously fake secrets: the app object needs them to be constructed, the schema never contains them.
    settings = Settings(
        env="test",
        openapi_mode="off",
        database_url=None,
        jwt_secret=SecretStr("export-only-" + "j" * 40),
        token_hash_key=SecretStr("export-only-" + "h" * 40),
    )
    schema: dict[str, Any] = create_app(settings).openapi()
    return schema


def render() -> str:
    return json.dumps(build_schema(), indent=2, sort_keys=True, ensure_ascii=False) + "\n"
