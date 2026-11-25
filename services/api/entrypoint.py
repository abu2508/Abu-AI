"""Container entrypoint:  uvicorn --factory services.api.entrypoint:create_checked_app

Staging and production: the deployment guard runs before anything else and the application verifies, at startup, that
its database role is a least-privilege runtime role. Development and test: identical to `create_app` (nothing extra).
"""

from fastapi import FastAPI

from services.api.config import get_settings
from services.api.deployment_guard import GUARDED_ENVS, assert_least_privilege, enforce
from services.api.main import create_app


def create_checked_app() -> FastAPI:
    settings = get_settings()
    enforce(settings)
    return create_app(
        settings, startup_check=assert_least_privilege if settings.env in GUARDED_ENVS else None
    )
