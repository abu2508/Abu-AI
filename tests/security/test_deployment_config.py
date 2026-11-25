"""Deployment configuration invariants (P1.3, P1.4): what the staging and production Compose layers may and may not do.

Static checks always run. Checks on the RESOLVED configuration use `docker compose config`, which needs the docker CLI
(no daemon, no network, no containers); they are skipped, with the reason, where the CLI is absent.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
INFRA = ROOT / "infrastructure"
LAYERS = {
    "dev": ["docker-compose.yml", "docker-compose.dev.yml"],
    "staging": ["docker-compose.yml", "docker-compose.hardened.yml", "docker-compose.staging.yml"],
    "production": [
        "docker-compose.yml",
        "docker-compose.hardened.yml",
        "docker-compose.production.yml",
    ],
}
PLACEHOLDER_ENV = """POSTGRES_ADMIN_USER=placeholder
POSTGRES_ADMIN_PASSWORD=placeholder
APP_DB_NAME=placeholder
APP_DB_USER=placeholder
APP_DB_PASSWORD=placeholder
APP_DB_OWNER_USER=placeholder_owner
APP_DB_OWNER_PASSWORD=placeholder
LITELLM_DB_NAME=placeholder
LITELLM_DB_USER=placeholder
LITELLM_DB_PASSWORD=placeholder
LITELLM_MASTER_KEY=sk-placeholder
JWT_SECRET=placeholder-placeholder-placeholder-00
TOKEN_HASH_KEY=placeholder-placeholder-placeholder-11
ABU_DOMAIN=example.invalid
ABU_ACME_EMAIL=ops@example.invalid
"""


class _ComposeLoader(yaml.SafeLoader):
    """Compose-only tags (!override, !reset) are plain YAML for this purpose: load the value they wrap."""


def _compose_tag(loader: yaml.SafeLoader, tag_suffix: str, node: yaml.Node) -> object:
    if isinstance(node, yaml.MappingNode):
        return loader.construct_mapping(node, deep=True)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node, deep=True)
    return loader.construct_scalar(node)  # type: ignore[arg-type]


_ComposeLoader.add_multi_constructor("!", _compose_tag)
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}\b")
needs_docker = pytest.mark.skipif(
    shutil.which("docker") is None, reason="docker CLI not available (compose config not rendered)"
)


def _resolved(
    env: str, tmp_path: Path, profile: bool = True, extra_env: str = ""
) -> dict[str, Any]:
    envfile = tmp_path / "placeholder.env"
    envfile.write_text(PLACEHOLDER_ENV + extra_env)
    cmd = ["docker", "compose", "--env-file", str(envfile)]
    if profile:
        cmd += ["--profile", "admin"]
    for f in LAYERS[env]:
        cmd += ["-f", f]
    cmd += ["config", "--format", "json"]
    out = subprocess.run(cmd, cwd=INFRA, capture_output=True, text=True, check=True)  # noqa: S603
    parsed: dict[str, Any] = json.loads(out.stdout)
    return parsed


# ------------------------------------------------------------------------------------------------ static
def test_every_image_and_base_image_is_pinned_by_digest() -> None:
    refs = []
    for f in INFRA.glob("docker-compose*.yml"):
        doc = yaml.load(f.read_text(), Loader=_ComposeLoader) or {}  # noqa: S506 - trusted repo file
        for svc in doc.get("services", {}).values():
            if isinstance(svc, dict) and "image" in svc:
                refs.append((f.name, svc["image"]))
    assert refs, "no images found"
    for name, ref in refs:
        assert DIGEST.search(ref), f"{name}: {ref} is not pinned by digest"
    dockerfile = (ROOT / "services/api/Dockerfile").read_text()
    froms = re.findall(r"^FROM\s+(\S+)", dockerfile, re.M) + re.findall(
        r"COPY --from=(\S+)", dockerfile
    )
    external = [r for r in froms if r not in ("base", "runtime")]
    assert external
    for ref in external:
        assert DIGEST.search(ref), f"Dockerfile: {ref} is not pinned by digest"


def test_dockerfile_records_release_identity_and_starts_the_guarded_entrypoint() -> None:
    d = (ROOT / "services/api/Dockerfile").read_text()
    assert "org.opencontainers.image.revision" in d and "ARG GIT_SHA" in d and "ARG VERSION" in d
    assert "services.api.entrypoint:create_checked_app" in d
    assert "USER abu" in d and "--no-server-header" in d and "--timeout-graceful-shutdown" in d
    # no package manager in the runtime image: its vendored libraries are inherited advisories and a tool for an attacker
    assert "pip uninstall -y pip" in d and "site-packages/pip" in d
    assert d.index("uv sync") < d.index(
        "pip uninstall"
    )  # dependencies are installed first, then pip is removed


@pytest.mark.parametrize("name", ["Caddyfile.staging", "Caddyfile.production"])
def test_caddyfile_hardening(name: str) -> None:
    text = (INFRA / "caddy" / name).read_text()
    assert "admin off" in text  # no admin API
    assert (
        "-Server" in text
        and "X-Content-Type-Options" in text
        and "Strict-Transport-Security" in text
    )
    assert "request_body" in text and "max_size" in text
    assert "reverse_proxy api:8000" in text
    for sensitive in ("Authorization", "Cookie", "Idempotency-Key"):
        assert f"request>headers>{sensitive} delete" in text, f"{name} logs {sensitive}"
    assert "request>remote_ip delete" in text
    # Caddy must trust no upstream proxy, otherwise a client-supplied X-Forwarded-For could reach the API's rate limiter
    assert "trusted_proxies" not in re.sub(r"#.*", "", text)


def test_production_caddyfile_has_no_hard_coded_domain_and_staging_is_not_public() -> None:
    prod = (INFRA / "caddy" / "Caddyfile.production").read_text()
    assert "{$ABU_DOMAIN}" in prod and "{$ABU_ACME_EMAIL}" in prod
    stg = (INFRA / "caddy" / "Caddyfile.staging").read_text()
    assert "https://localhost" in stg and "local_certs" in stg
    stg_compose = (INFRA / "docker-compose.staging.yml").read_text()
    assert '"127.0.0.1:${STAGING_HTTPS_PORT:-8443}:443"' in stg_compose
    assert "0.0.0.0" not in stg_compose  # noqa: S104 - asserting it is absent


def test_production_overlay_cannot_publish_without_an_explicit_domain() -> None:
    text = (INFRA / "docker-compose.production.yml").read_text()
    assert "${ABU_DOMAIN:?" in text and "${ABU_ACME_EMAIL:?" in text
    assert "NOT AUTHORISED" in text


def test_init_script_only_changes_behaviour_when_the_owner_role_is_configured() -> None:
    sh = (INFRA / "postgres/01-init.sh").read_text()
    assert 'if [ -n "${APP_DB_OWNER_USER:-}" ]; then' in sh
    assert "NOSUPERUSER NOCREATEDB NOCREATEROLE" in sh
    assert (
        'CREATE DATABASE :"app_db" OWNER :"own_user"' in sh
    )  # split mode: the owner role owns the database
    assert 'CREATE DATABASE :"app_db" OWNER :"app_user"' in sh  # legacy mode unchanged


# ------------------------------------------------------------------------------------------------ resolved
@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_the_api_is_internal_only_and_has_no_internet_route(env: str, tmp_path: Path) -> None:
    cfg = _resolved(env, tmp_path)
    api = cfg["services"]["api"]
    assert not api.get("ports"), "the API must publish no port in staging/production"
    assert set(api["networks"]) == {"data", "proxy"}
    for net in ("data", "proxy"):
        assert cfg["networks"][net]["internal"] is True, f"{net} must be an internal network"
    assert set(cfg["services"]["caddy"]["networks"]) == {"proxy", "edge"}


@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_the_model_gateway_has_no_internet_route(env: str, tmp_path: Path) -> None:
    cfg = _resolved(env, tmp_path)
    assert set(cfg["services"]["litellm"]["networks"]) == {"data"}
    assert "OPENAI_API_KEY" not in str(
        cfg["services"]["litellm"].get("environment")
    )  # no provider keys either


@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_only_caddy_publishes_ports_and_the_database_and_gateway_never_do(
    env: str, tmp_path: Path
) -> None:
    cfg = _resolved(env, tmp_path)
    published = {n for n, s in cfg["services"].items() if s.get("ports")}
    assert published == {"caddy"}, published


@needs_docker
def test_staging_publishes_loopback_only(tmp_path: Path) -> None:
    for p in _resolved("staging", tmp_path)["services"]["caddy"]["ports"]:
        assert p["host_ip"] == "127.0.0.1" and p["target"] == 443


@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_owner_credentials_exist_only_in_the_one_shot_migrate_step(
    env: str, tmp_path: Path
) -> None:
    cfg = _resolved(env, tmp_path)
    for name, svc in cfg["services"].items():
        env_keys = set(svc.get("environment") or {})
        has_owner = "ABU_MIGRATION_DATABASE_URL" in env_keys
        assert has_owner == (name == "migrate"), f"{name}: owner URL presence is wrong"
        blob = json.dumps(svc.get("environment") or {})
        if name in ("api", "admin"):
            assert "placeholder_owner" not in blob, f"{name} sees the owner role"
    migrate = cfg["services"]["migrate"]
    assert migrate["restart"] == "no"
    assert migrate["command"] == ["python", "-m", "services.admin", "db", "bootstrap"]
    assert not {"ABU_JWT_SECRET", "ABU_TOKEN_HASH_KEY", "ABU_LITELLM_MASTER_KEY"} & set(
        migrate["environment"]
    ), "the migration step must not receive application secrets"
    assert "migrate" in cfg["services"]["api"]["depends_on"]
    assert (
        cfg["services"]["api"]["depends_on"]["migrate"]["condition"]
        == "service_completed_successfully"
    )


@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_api_runtime_settings_satisfy_the_deployment_guard(env: str, tmp_path: Path) -> None:
    api = _resolved(env, tmp_path)["services"]["api"]["environment"]
    assert api["ABU_ENV"] == env
    assert api["ABU_TRUSTED_PROXY_HOPS"] == "1"
    assert api["ABU_CHECKPOINT_AUTO_SETUP"] == "false"
    assert api["ABU_MEMORY_KEYRING_FILE"] == "/run/secrets/memory_keyring"
    assert (
        "ABU_MEMORY_KEK" not in api and "ABU_OPENAPI_MODE" not in api
    )  # never enabled by configuration


@needs_docker
@pytest.mark.parametrize("env", ["staging", "production"])
def test_container_hardening_flags(env: str, tmp_path: Path) -> None:
    cfg = _resolved(env, tmp_path)
    for name, svc in cfg["services"].items():
        assert not svc.get("privileged"), name
        assert svc.get("network_mode") not in ("host",), name
        assert svc.get("pid") != "host" and svc.get("ipc") != "host", name
        assert "no-new-privileges:true" in svc.get("security_opt", []), f"{name}: no-new-privileges"
        assert "ALL" in svc.get("cap_drop", []), f"{name}: capabilities not dropped"
        assert svc.get("pids_limit"), f"{name}: no pids limit"
        for v in svc.get("volumes") or []:
            assert "docker.sock" not in str(v.get("source", "")), f"{name} mounts the docker socket"
    for name in ("api", "migrate", "admin", "caddy"):
        assert cfg["services"][name].get("read_only") is True, (
            f"{name}: root filesystem not read-only"
        )
    for name in ("api", "migrate", "admin", "caddy", "db", "litellm"):
        assert cfg["services"][name].get("mem_limit") or cfg["services"][name].get("deploy"), name


@needs_docker
def test_the_database_receives_the_owner_role_only_through_the_init_script_environment(
    tmp_path: Path,
) -> None:
    db = _resolved("staging", tmp_path)["services"]["db"]
    assert db["environment"]["APP_DB_OWNER_USER"] == "placeholder_owner"
    assert not db.get("ports")


@needs_docker
def test_production_overlay_refuses_to_resolve_without_a_domain(tmp_path: Path) -> None:
    envfile = tmp_path / "e.env"
    envfile.write_text(
        "\n".join(ln for ln in PLACEHOLDER_ENV.splitlines() if "ABU_DOMAIN" not in ln)
    )
    cmd = ["docker", "compose", "--env-file", str(envfile)]
    for f in LAYERS["production"]:
        cmd += ["-f", f]
    done = subprocess.run(cmd + ["config", "--quiet"], cwd=INFRA, capture_output=True, text=True)  # noqa: S603
    assert done.returncode != 0 and "ABU_DOMAIN" in done.stderr


@needs_docker
def test_development_layer_is_unchanged_by_the_hardening(tmp_path: Path) -> None:
    cfg = _resolved("dev", tmp_path)
    assert cfg["services"]["api"]["ports"][0]["host_ip"] == "127.0.0.1"
    assert "caddy" not in cfg["services"]
    assert cfg["services"]["api"]["environment"]["ABU_ENV"] == "development"
