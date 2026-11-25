"""Environment-driven configuration. Secrets are SecretStr and never logged."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

MIN_SECRET_CHARS = 32


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ABU_", env_file=None, extra="ignore")

    # "staging" is production-like (fail-closed deployment guard, keyring FILE, no OpenAPI) but may run with synthetic data
    # and the explicitly allowed test embedder. Only "production" refuses the test embedder outright (gate G4).
    env: Literal["development", "test", "staging", "production"] = "development"
    # OpenAPI is OFF everywhere by default. "authenticated" serves the schema at /v1/dev/openapi.json behind the
    # normal bearer authentication (development/test only). Swagger UI and ReDoc do not exist in any mode.
    openapi_mode: Literal["off", "authenticated"] = "off"
    # SQLAlchemy async URL, e.g. postgresql+psycopg://user:pw@db:5432/abu_ai
    # This is the RUNTIME role: in staging/production it must have no DDL privileges (see docs/operations/DATABASE_ROLES.md).
    database_url: SecretStr | None = None
    # The OWNER role used only by the one-shot migration/bootstrap step (alembic, checkpoint tables, grants). It must
    # NOT be present in the API or admin runtime environment; the deployment guard refuses to start if it is.
    migration_database_url: SecretStr | None = None
    # LangGraph's saver creates its tables at startup (DDL). A least-privilege runtime role cannot do that, so
    # staging/production set this to false and the bootstrap step creates the tables as the owner.
    checkpoint_auto_setup: bool = True
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
    max_import_bytes: int = Field(
        default=3 * 1024 * 1024, ge=1024, le=10 * 1024 * 1024
    )  # file-import routes only
    # Number of trusted reverse proxies (Caddy = 1) whose X-Forwarded-For hop we believe.
    # 0 (default): ignore X-Forwarded-For entirely, use the socket peer.
    trusted_proxy_hops: int = Field(default=0, ge=0, le=5)
    rate_limit_auth_per_minute: int = Field(default=10, ge=1)
    rate_limit_ip_per_minute: int = Field(default=120, ge=1)
    rate_limit_user_per_minute: int = Field(default=300, ge=1)

    # --- memory (Milestone 5) -------------------------------------------------------
    # Master-key material for envelope encryption. It lives OUTSIDE PostgreSQL: either a JSON
    # keyring file (preferred; mount it read-only, mode 600) or a single base64 key in the env
    # (development). Never stored in the database, never in the repository.
    memory_keyring_file: str | None = None
    memory_kek: SecretStr | None = None
    # Docker-secret mounts are world-readable inside the container (access is controlled on the host
    # and by the mount), so compose sets this false. Keep it true for ordinary file paths.
    memory_keyring_strict_permissions: bool = True
    # Embeddings. Only the deterministic local TEST embedder exists. Any other provider is refused
    # until explicitly approved (data policy, retention, training, pricing) - see core/memory/embeddings.py
    embedder: str = "local-test-hash"
    embedding_external_approved: bool = False
    allow_test_embedder_in_production: bool = False
    # Retention (days). 0 / None = keep until deleted.
    retention_proposal_days: int = Field(default=2, ge=1, le=30)
    retention_third_party_days: int = Field(default=90, ge=1, le=3650)
    retention_episodic_days: int = Field(default=180, ge=1, le=3650)
    retention_superseded_days: int = Field(default=30, ge=1, le=3650)
    retention_thread_days: int = Field(default=7, ge=1, le=365)
    # Orchestrator: how long an idempotent response can be replayed (encrypted), and how long run metadata is kept.
    orchestrator_response_ttl_hours: int = Field(default=24, ge=1, le=168)
    orchestrator_run_retention_days: int = Field(default=30, ge=1, le=365)

    @field_validator("jwt_secret", "token_hash_key")
    @classmethod
    def _strong_secret(cls, v: SecretStr | None) -> SecretStr | None:
        if v is not None and len(v.get_secret_value()) < MIN_SECRET_CHARS:
            raise ValueError(f"secret must be at least {MIN_SECRET_CHARS} characters")
        return v

    @model_validator(mode="after")
    def _openapi_never_in_production(self) -> "Settings":
        """Fail closed: the schema may be served (authenticated) only in development/test, never in production."""
        if self.env in ("production", "staging") and self.openapi_mode != "off":
            raise ValueError("ABU_OPENAPI_MODE must be 'off' when ABU_ENV is production or staging")
        return self

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
