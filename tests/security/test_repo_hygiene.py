"""Static checks that keep secrets and exposure rules from regressing."""

import re
import subprocess
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMPOSE = yaml.safe_load((ROOT / "infrastructure/docker-compose.yml").read_text())
DEV_COMPOSE = yaml.safe_load((ROOT / "infrastructure/docker-compose.dev.yml").read_text())


def _published_ports(compose: dict) -> list[str]:  # type: ignore[type-arg]
    out: list[str] = []
    for svc in compose["services"].values():
        out += [str(p) for p in svc.get("ports", [])]
    return out


def test_db_publishes_no_ports_in_base_compose() -> None:
    assert "ports" not in COMPOSE["services"]["db"]


def test_litellm_publishes_no_ports_in_base_compose() -> None:
    assert "ports" not in COMPOSE["services"]["litellm"]


def test_all_published_ports_are_loopback_only() -> None:
    for p in _published_ports(COMPOSE) + _published_ports(DEV_COMPOSE):
        assert p.startswith("127.0.0.1:"), f"{p} is not bound to loopback"


def test_db_only_on_internal_network() -> None:
    assert COMPOSE["networks"]["data"]["internal"] is True
    assert COMPOSE["services"]["db"]["networks"] == ["data"]


def test_litellm_ui_flag_set_and_no_provider_keys() -> None:
    env = COMPOSE["services"]["litellm"]["environment"]
    assert env["DISABLE_ADMIN_UI"] == "True"
    assert not [k for k in env if k.endswith("_API_KEY")]


LITELLM_CFG = yaml.safe_load((ROOT / "services/litellm/config.yaml").read_text())


def test_litellm_has_only_mock_models() -> None:
    """No real provider may be configured: every model must short-circuit with mock_response."""
    assert LITELLM_CFG["model_list"], "expected the mock model"
    for entry in LITELLM_CFG["model_list"]:
        assert "mock_response" in entry["litellm_params"], entry["model_name"]


def test_litellm_master_key_from_env() -> None:
    assert LITELLM_CFG["general_settings"]["master_key"].startswith("os.environ/")


def test_litellm_max_budget_is_positive() -> None:
    """LiteLLM treats max_budget <= 0 as 'no limit' (checks are guarded by `> 0`)."""
    assert LITELLM_CFG["litellm_settings"]["max_budget"] > 0


def test_litellm_ui_stub_is_inert() -> None:
    stub = ROOT / "services/litellm/ui-disabled"
    assert [p.name for p in stub.iterdir() if p.name != "_next"] == ["index.html"]
    assert [p.name for p in (stub / "_next").iterdir()] == [".gitkeep"]
    mounts = COMPOSE["services"]["litellm"]["volumes"]
    assert any(m.endswith("site-packages/litellm/proxy/_experimental/out:ro") for m in mounts)


def test_env_example_has_only_placeholders() -> None:
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if re.match(r"^[A-Z_]*(PASSWORD|KEY|SECRET)=", line):
            assert "change-me" in line, line


def test_env_file_is_gitignored() -> None:
    res = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", ".env"],  # noqa: S607
        cwd=ROOT,
        check=False,
    )
    assert res.returncode == 0


def test_admin_service_is_opt_in_unpublished_and_internal_only() -> None:
    admin = COMPOSE["services"]["admin"]
    assert admin["profiles"] == ["admin"]  # never started by a plain `up`
    assert "ports" not in admin
    assert admin["networks"] == ["data"]  # no internet route


def test_auth_secrets_come_from_env_not_literals() -> None:
    for svc in ("api", "admin"):
        env = COMPOSE["services"][svc]["environment"]
        for key in ("ABU_JWT_SECRET", "ABU_TOKEN_HASH_KEY"):
            assert env[key].startswith("${") and "set in .env" in env[key], (svc, key)


def test_mobile_facing_api_does_not_receive_admin_or_provider_credentials() -> None:
    env = COMPOSE["services"]["api"]["environment"]
    assert not [k for k in env if k.endswith("_API_KEY")]
    assert "POSTGRES_ADMIN_PASSWORD" not in str(env)  # app uses its least-privilege DB role


def test_trusted_proxy_hops_defaults_to_zero() -> None:
    assert COMPOSE["services"]["api"]["environment"]["ABU_TRUSTED_PROXY_HOPS"].endswith(":-0}")


def test_source_has_no_embedded_secrets_or_master_key() -> None:
    pattern = re.compile(
        r"(sk-[A-Za-z0-9]{20,}|AIza[0-9A-Za-z_-]{30,}|-----BEGIN [A-Z ]*PRIVATE KEY-----)"
    )
    for path in list((ROOT / "services").rglob("*.py")) + list((ROOT / "core").rglob("*.py")):
        assert not pattern.search(path.read_text()), path


def test_api_image_does_not_advertise_server_software() -> None:
    dockerfile = (ROOT / "services/api/Dockerfile").read_text()
    assert "--no-server-header" in dockerfile and "--factory" in dockerfile
