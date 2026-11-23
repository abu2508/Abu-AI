import pytest

from services.api.config import Settings


def test_defaults_are_safe() -> None:
    s = Settings()
    assert s.env == "development"
    assert s.database_url is None


def test_secret_not_in_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ABU_DATABASE_URL", "postgresql+asyncpg://u:hunter2@db/x")
    s = Settings()
    assert "hunter2" not in repr(s)
    assert "hunter2" not in str(s)
    assert s.database_url is not None
    assert "hunter2" in s.database_url.get_secret_value()
