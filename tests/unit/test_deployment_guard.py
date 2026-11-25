"""The fail-closed deployment guard (P1.3): staging and production refuse unsafe or incomplete configuration, while the
development and test defaults stay exactly as they were. Messages name settings and rules, never values."""

import pytest
from pydantic import SecretStr

from services.api.config import Settings
from services.api.deployment_guard import DeploymentConfigError, deployment_problems, enforce
from services.api.entrypoint import create_checked_app

JWT = "j" * 40
HASH = "h" * 40
SECRET_MARKERS = (JWT, HASH, "sk-litellm-marker", "owner-password-marker", "kek-marker")


def good(env: str = "staging", **over: object) -> Settings:
    base: dict[str, object] = {
        "env": env,
        "database_url": SecretStr("postgresql+psycopg://rt:pw@db:5432/abu_ai"),
        "jwt_secret": SecretStr(JWT),
        "token_hash_key": SecretStr(HASH),
        "litellm_master_key": SecretStr("sk-litellm-marker"),
        "memory_keyring_file": "/run/secrets/memory_keyring",
        "trusted_proxy_hops": 1,
        "checkpoint_auto_setup": False,
        "log_format": "json",
        "allow_test_embedder_in_production": True,  # staging's explicit "synthetic data only" statement
    }
    return Settings(**{**base, **over})  # type: ignore[arg-type]


def test_development_and_test_defaults_are_unchanged() -> None:
    assert Settings().env == "development"
    assert Settings().checkpoint_auto_setup is True
    assert Settings().migration_database_url is None
    for env in ("development", "test"):
        assert deployment_problems(Settings(env=env)) == []
        enforce(Settings(env=env))  # nothing is required in these environments


def test_a_complete_staging_configuration_passes() -> None:
    assert deployment_problems(good()) == []
    enforce(good())


@pytest.mark.parametrize(
    ("over", "fragment"),
    [
        ({"database_url": None}, "ABU_DATABASE_URL"),
        ({"jwt_secret": None}, "ABU_JWT_SECRET"),
        ({"token_hash_key": None}, "ABU_TOKEN_HASH_KEY"),
        ({"token_hash_key": SecretStr(JWT)}, "must differ"),
        ({"litellm_master_key": None}, "ABU_LITELLM_MASTER_KEY"),
        ({"memory_keyring_file": None}, "ABU_MEMORY_KEYRING_FILE"),
        ({"memory_kek": SecretStr("kek-marker-" + "k" * 30)}, "ABU_MEMORY_KEK"),
        ({"trusted_proxy_hops": 0}, "ABU_TRUSTED_PROXY_HOPS"),
        ({"trusted_proxy_hops": 2}, "ABU_TRUSTED_PROXY_HOPS"),
        (
            {
                "migration_database_url": SecretStr(
                    "postgresql+psycopg://owner:owner-password-marker@db/x"
                )
            },
            "ABU_MIGRATION_DATABASE_URL",
        ),
        ({"checkpoint_auto_setup": True}, "ABU_CHECKPOINT_AUTO_SETUP"),
        ({"log_format": "text"}, "ABU_LOG_FORMAT"),
        ({"allow_test_embedder_in_production": False}, "TEST embedder"),
        ({"embedder": "does-not-exist"}, "unknown embedder"),
    ],
)
@pytest.mark.parametrize("env", ["staging", "production"])
def test_each_unsafe_setting_stops_the_start_in_staging_and_production(
    env: str, over: dict[str, object], fragment: str
) -> None:
    s = good(env, **over)
    problems = deployment_problems(s)
    assert any(fragment in p for p in problems), problems
    with pytest.raises(DeploymentConfigError) as err:
        enforce(s)
    text = str(err.value)
    assert f"ABU_ENV={env}" in text
    for marker in SECRET_MARKERS:
        assert marker not in text, "a secret value appeared in the refusal message"


def test_production_refuses_the_test_embedder_even_when_the_staging_override_is_set() -> None:
    problems = deployment_problems(
        good("production")
    )  # allow_test_embedder_in_production=True is ignored here
    assert any("G4" in p and "refused in production" in p for p in problems), problems
    with pytest.raises(DeploymentConfigError):
        enforce(good("production"))


def test_openapi_stays_off_in_staging_as_in_production() -> None:
    with pytest.raises(ValueError, match="ABU_OPENAPI_MODE must be 'off'"):
        Settings(env="staging", openapi_mode="authenticated")


def test_all_problems_are_reported_at_once() -> None:
    s = good("production", database_url=None, litellm_master_key=None, trusted_proxy_hops=0)
    assert len(deployment_problems(s)) >= 4  # an operator fixes the whole list in one pass


def test_the_checked_factory_refuses_before_building_the_app(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for k, v in {
        "ABU_ENV": "production",
        "ABU_JWT_SECRET": JWT,
        "ABU_TOKEN_HASH_KEY": HASH,
    }.items():
        monkeypatch.setenv(k, v)
    from services.api.config import get_settings

    get_settings.cache_clear()
    try:
        with pytest.raises(DeploymentConfigError):
            create_checked_app()
    finally:
        get_settings.cache_clear()


def test_the_checked_factory_is_plain_create_app_in_development(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ABU_ENV", "development")
    monkeypatch.setenv("ABU_JWT_SECRET", JWT)
    monkeypatch.setenv("ABU_TOKEN_HASH_KEY", HASH)
    from services.api.config import get_settings

    get_settings.cache_clear()
    try:
        app = create_checked_app()
        assert app.title == "Abu AI"
    finally:
        get_settings.cache_clear()
