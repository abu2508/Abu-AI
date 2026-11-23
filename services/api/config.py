"""Environment-driven configuration. Secrets are SecretStr and never logged."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_CHARS = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ABU_", env_file=None, extra="ignore")

    env: Literal["development", "test", "production"] = "development"
    # SQLAlchemy async URL, e.g. postgresql+psycopg://user:pw@db:5432/abu_ai
    database_url: SecretStr | None = None
    litellm_master_key: SecretStr | None = None
    litellm_base_url: str = "http://litellm:4000"
    # Short timeouts so health checks never hang the API.
    dependency_timeout_seconds: float = 3.0

    # --- authentication -------------------------------------------------------------
    # Signs access JWTs. Independent from token_hash_key so one leak does not expose both.
    jwt_secret: SecretStr | None = None
    # Keys the HMAC used to hash refresh tokens and enrollment codes before storage.
    token_hash_key: SecretStr | None = None
    jwt_issuer: str = "abu-ai"
    jwt_audience: str = "abu-ai-api"
    access_token_ttl_seconds: int = Field(default=600, ge=60, le=3600)
    refresh_token_ttl_days: int = Field(default=30, ge=1, le=90)
    session_max_days: int = Field(default=90, ge=1, le=365)
    enrollment_code_ttl_minutes: int = Field(default=10, ge=1, le=60)

    # --- request hardening ----------------------------------------------------------
    max_request_bytes: int = Field(default=64 * 1024, ge=1024, le=10 * 1024 * 1024)
    # Number of trusted reverse proxies (Caddy = 1) whose X-Forwarded-For hop we believe.
    # 0 (default): ignore X-Forwarded-For entirely, use the socket peer.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    rate_limit_auth_per_minute: int = Field(default=10, ge=1)
    rate_limit_ip_per_minute: int = Field(default=120, ge=1)
    rate_limit_user_per_minute: int = Field(default=300, ge=1)

    @field_validator("jwt_secret", "token_hash_key")
    @classmethod
    def _strong_secret(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < MIN_SECRET_CHARS:
            raise ValueError(f"secret must be at least {MIN_SECRET_CHARS} characters")
        return v

    @property
    def docs_enabled(self) -> bool:
        return self.env != "production"

    def require_auth_secrets(self) -> tuple[SecretStr, SecretStr]:
        """Fail fast at startup instead of running with unauthenticated or weak auth."""
        if self.jwt_secret is None or self.token_hash_key is None:
            raise RuntimeError("ABU_JWT_SECRET and ABU_TOKEN_HASH_KEY must be set")
        if self.jwt_secret.get_secret_value() == self.token_hash_key.get_secret_value():
            raise RuntimeError("ABU_JWT_SECRET and ABU_TOKEN_HASH_KEY must differ")
        return self.jwt_secret, self.token_hash_key


@lru_cache
def get_settings() -> Settings:
    return Settings()
