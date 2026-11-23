"""Environment-driven configuration. Secrets are SecretStr and never logged."""

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ABU_", env_file=None, extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    # SQLAlchemy async URL, e.g. postgresql+psycopg://user:pw@db:5432/abu_ai
    database_url: SecretStr | None = None
    litellm_master_key: SecretStr | None = None
    litellm_base_url: str = "http://litellm:4000"
    # Short timeouts so health checks never hang the API.
    dependency_timeout_seconds: float = 3.0

    @property
    def docs_enabled(self) -> bool:
        return self.env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
