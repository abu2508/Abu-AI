import os
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "infrastructure/scripts/security-gates.sh"


def _run(**env: str) -> subprocess.CompletedProcess[str]:
    clean = {k: v for k, v in os.environ.items() if not k.startswith("ABU_GATE_")}
    return subprocess.run(
        ["bash", str(SCRIPT)], env={**clean, **env}, capture_output=True, text=True, check=False
    )


def test_gates_are_blocked_by_default() -> None:
    r = _run()
    assert (
        r.returncode == 1
        and r.stdout.count("BLOCKED") == 4
        and "Do NOT use real personal" in r.stdout
    )


def test_gates_pass_only_when_all_acknowledged() -> None:
    ok = {
        "ABU_GATE_DISK_ENCRYPTION": "verified",
        "ABU_GATE_KEYRING_BACKUP": "verified",
        "ABU_GATE_CHECKPOINT_METADATA": "accepted",
        "ABU_GATE_PRODUCTION_EMBEDDER": "verified",
    }
    assert _run(**ok).returncode == 0
    partial = dict(ok)
    partial.pop("ABU_GATE_KEYRING_BACKUP")
    assert _run(**partial).returncode == 1
