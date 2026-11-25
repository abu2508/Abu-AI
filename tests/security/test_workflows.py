# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""GitHub Actions hygiene (P1.7): third-party code is pinned to immutable commits, workflows get least privilege, jobs
cannot hang forever, and nothing builds a shell command from attacker-controlled event data."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = sorted((ROOT / ".github/workflows").glob("*.yml"))
USES = re.compile(r"^\s*-?\s*uses:\s*(\S+)(?:\s*#\s*(\S.*))?$", re.M)
SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")
DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")


def _load(path: Path) -> dict[Any, Any]:
    doc = yaml.safe_load(path.read_text())
    assert isinstance(doc, dict)
    return doc


def test_workflows_exist() -> None:
    assert {w.name for w in WORKFLOWS} >= {
        "backend.yml",
        "backend-openapi.yml",
        "mobile.yml",
        "security.yml",
    }


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_action_is_pinned_to_a_full_commit_sha_with_a_version_comment(path: Path) -> None:
    for ref, comment in USES.findall(path.read_text()):
        if ref.startswith("./"):
            continue
        assert SHA.match(ref), f"{path.name}: {ref} is not pinned to a 40-character commit SHA"
        assert comment and re.match(r"v?\d", comment), (
            f"{path.name}: {ref} has no '# vX.Y.Z' version comment"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_container_images_used_by_workflows_are_pinned_by_digest(path: Path) -> None:
    doc = _load(path)
    env = doc.get("env") or {}
    assert isinstance(env, dict)
    for name, value in env.items():
        if name.endswith("_IMAGE"):
            assert DIGEST.search(str(value)), f"{path.name}: {name} is not pinned by digest"
    text = path.read_text()
    assert not re.search(r"docker run[^\n]*\b(?:latest|main)\b", text), (
        f"{path.name}: floating image tag"
    )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_permissions_are_read_only_by_default(path: Path) -> None:
    doc = _load(path)
    perms = doc.get("permissions")
    assert perms == {"contents": "read"}, (
        f"{path.name}: top-level permissions must be exactly contents: read, got {perms!r}"
    )
    for job_name, job in (doc.get("jobs") or {}).items():
        jp = job.get("permissions")
        assert jp is None or all(v == "read" for v in jp.values()), (
            f"{path.name}:{job_name} requests write access"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_a_timeout(path: Path) -> None:
    for job_name, job in (_load(path).get("jobs") or {}).items():
        t = job.get("timeout-minutes")
        assert isinstance(t, int) and 1 <= t <= 60, (
            f"{path.name}:{job_name} needs timeout-minutes (1-60)"
        )


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_privileged_triggers_and_no_event_data_in_shell_commands(path: Path) -> None:
    doc = _load(path)
    triggers = doc.get(True) or doc.get("on") or {}  # YAML parses a bare `on:` key as boolean True
    assert "pull_request_target" not in triggers and "workflow_run" not in triggers, path.name
    for job_name, job in (doc.get("jobs") or {}).items():
        for step in job.get("steps", []):
            run = step.get("run", "")
            bad = re.findall(r"\$\{\{\s*github\.(?:event|head_ref)[^}]*\}\}", run)
            assert not bad, (
                f"{path.name}:{job_name} interpolates event data into a shell command: {bad}"
            )
            assert "secrets." not in run, (
                f"{path.name}:{job_name} puts a secret straight into a shell command"
            )


def test_dependabot_covers_every_ecosystem_in_use() -> None:
    cfg = yaml.safe_load((ROOT / ".github/dependabot.yml").read_text())
    eco = {u["package-ecosystem"]: u["directory"] for u in cfg["updates"]}
    assert eco == {
        "github-actions": "/",
        "uv": "/",
        "npm": "/mobile",
        "docker": "/services/api",
        "docker-compose": "/infrastructure",
    }
    assert all(u["schedule"]["interval"] == "weekly" for u in cfg["updates"])


def test_the_security_workflow_has_the_checks_the_release_procedure_requires() -> None:
    jobs = set(_load(ROOT / ".github/workflows/security.yml")["jobs"])
    assert jobs == {"dependency-audit", "sast", "secret-scan", "image-scan", "actions-lint"}
    release = (ROOT / "docs/operations/RELEASE.md").read_text()
    for j in jobs:
        assert f"`{j}`" in release, f"RELEASE.md does not list the required check {j}"
