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


def test_memory_keys_are_a_docker_secret_file_never_an_env_value_and_never_in_the_database() -> (
    None
):
    api = COMPOSE["services"]["api"]
    assert api["secrets"] == ["memory_keyring"] and COMPOSE["services"]["admin"]["secrets"] == [
        "memory_keyring"
    ]
    assert COMPOSE["secrets"]["memory_keyring"]["file"] == "../secrets/memory-keyring.json"
    env = api["environment"]
    assert env["ABU_MEMORY_KEYRING_FILE"] == "/run/secrets/memory_keyring"
    assert "ABU_MEMORY_KEK" not in env  # no raw key in an environment variable
    assert (
        "memory" not in str(COMPOSE["services"]["db"]["environment"]).lower()
    )  # the database never receives key material


def test_keyring_and_backup_keys_are_git_ignored() -> None:
    for path in ("secrets/memory-keyring.json", "backups/db.dump.enc", "x.key"):
        res = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT, check=False)  # noqa: S603, S607
        assert res.returncode == 0, path


def test_embedder_defaults_are_the_local_test_embedder_and_production_use_is_gated() -> None:
    env = COMPOSE["services"]["api"]["environment"]
    assert env["ABU_EMBEDDER"] == "local-test-hash"
    assert env["ABU_ALLOW_TEST_EMBEDDER_IN_PRODUCTION"].endswith(
        ":-false}"
    )  # opt-in, default false
    assert (
        "false"
        in (ROOT / ".env.example")
        .read_text()
        .split("ALLOW_TEST_EMBEDDER_IN_PRODUCTION=")[1]
        .split()[0]
    )


def test_no_memory_column_can_hold_plaintext_content_or_key_material() -> None:
    from database.schemas import Base

    for table in ("memory_records", "memory_embeddings", "memory_access_logs", "agent_threads"):
        cols = {c.name for c in Base.metadata.tables[table].columns}
        assert not (
            cols
            & {
                "text",
                "content",
                "plaintext",
                "body",
                "message",
                "prompt",
                "kek",
                "key_material",
                "secret",
            }
        ), (table, cols)
    rec = Base.metadata.tables["memory_records"]
    assert (
        str(rec.c.content_enc.type).upper().startswith("BYTEA")
        or "LargeBinary" in type(rec.c.content_enc.type).__name__
    )


def test_encryption_at_rest_tooling_exists_and_is_honest() -> None:
    script = (ROOT / "infrastructure/scripts/verify-encryption-at-rest.sh").read_text()
    assert (
        "NOT VERIFIED" in script and "ENCRYPTED" in script
    )  # can fail; never reports success without evidence
    assert (
        subprocess.run(
            ["bash", "-n", str(ROOT / "infrastructure/scripts/verify-encryption-at-rest.sh")],
            check=False,
        ).returncode
        == 0
    )  # noqa: S603, S607
    assert (ROOT / "infrastructure/scripts/encrypted_backup.py").exists()


def test_api_and_admin_never_run_as_root_and_can_read_only_their_own_key_file() -> None:
    for svc in ("api", "admin"):
        user = COMPOSE["services"][svc]["user"]
        assert (
            user.endswith(":-10001}") and user.strip("${}").split(":-")[-1] != "0"
        )  # default is a non-root uid
    script = (ROOT / "infrastructure/scripts/gen-secrets.sh").read_text()
    assert "chmod 600 secrets/memory-keyring.json" in script and "ABU_UID" in script
