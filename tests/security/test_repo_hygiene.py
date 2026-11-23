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


def test_litellm_config_has_no_models_or_hardcoded_secrets() -> None:
    cfg = yaml.safe_load((ROOT / "services/litellm/config.yaml").read_text())
    assert cfg["model_list"] == []
    assert cfg["general_settings"]["master_key"].startswith("os.environ/")


def test_env_example_has_only_placeholders() -> None:
    text = (ROOT / ".env.example").read_text()
    for line in text.splitlines():
        if re.match(r"^[A-Z_]*(PASSWORD|KEY)=", line):
            assert line.endswith(("change-me", "sk-change-me")), line


def test_env_file_is_gitignored() -> None:
    res = subprocess.run(  # noqa: S603
        ["git", "check-ignore", "-q", ".env"],  # noqa: S607
        cwd=ROOT,
        check=False,
    )
    assert res.returncode == 0
