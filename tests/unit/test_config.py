import pytest
from pydantic import SecretStr, ValidationError

from services.api.config import Settings
from services.api.main import create_app


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.env == "development"
    assert s.database_url is None
    assert s.trusted_proxy_hops == 0  # X-Forwarded-For ignored by default


def test_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABU_DATABASE_URL", "postgresql+psycopg://u:hunter2@db/x")
    s = Settings()
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s)


def test_short_secrets_rejected() -> None:
    with pytest.raises(ValidationError):
        Settings(jwt_secret=SecretStr("short"))


def test_app_refuses_to_start_without_auth_secrets() -> None:
    with pytest.raises(RuntimeError, match="must be set"):
        create_app(Settings())


def test_app_refuses_identical_secrets() -> None:
    same = SecretStr("x" * 40)
    with pytest.raises(RuntimeError, match="must differ"):
        create_app(Settings(jwt_secret=same, token_hash_key=same))
