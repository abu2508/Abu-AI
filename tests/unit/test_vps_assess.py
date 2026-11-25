"""The VPS assessment script must stay read-only, secret-free and redacting. These tests guard those promises."""

import re
import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "infrastructure" / "scripts" / "vps-assess.sh"
SRC = SCRIPT.read_text()


def _code_lines() -> list[str]:
    """Executable text only: comments are dropped and quoted strings are blanked, so documentation text (an echo'd note,
    a grep pattern) cannot trigger a false positive, while a real redirection or command still would."""
    out = []
    for line in SRC.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        out.append(re.sub(r"'[^']*'|\"[^\"]*\"", "''", stripped))
    return out


def test_script_is_valid_bash() -> None:
    subprocess.run(["bash", "-n", str(SCRIPT)], check=True)


def test_script_cannot_change_the_host() -> None:
    forbidden = [
        r"\bapt(-get)?\b",
        r"\bdnf\b",
        r"\byum\b",
        r"\bpip\b",
        r"\bsystemctl\s+(start|stop|restart|enable|disable|mask|reload|daemon-reload)\b",
        r"\bservice\s+\S+\s+(start|stop|restart)\b",
        r"\bufw\s+(allow|deny|enable|disable|reset|delete|insert|limit)\b",
        r"\biptables\s+-[AIDFXRZP]\b",
        r"\bnft\s+(add|delete|flush|insert)\b",
        r"\bdocker\s+(run|exec|stop|rm|kill|start|restart|pull|build|rmi|prune|cp|commit)\b",
        r"\bdocker\s+compose\s+(up|down|run|exec|rm|stop|start|pull|build)\b",
        r"\b(curl|wget|nc|ncat|ssh|scp|rsync|telnet)\b",
        r"(?:^|[;&|(]|\$\()\s*(rm|mv|cp|chmod|chown|tee|dd|mkfs|mount|umount|truncate)\b",
        r"\bsed\s+-i\b",
        r"\buseradd\b|\busermod\b|\bpasswd\b",
        r"(?:^|\s)\d?>>?\s*(?!/dev/null\b|&)[/~.$\w\"']",  # a shell redirection writing to a file
    ]
    for line in _code_lines():
        for pat in forbidden:
            assert not re.search(pat, line), (
                f"mutating command in read-only script: {line!r} ~ {pat}"
            )


def test_script_never_reads_secret_or_private_material() -> None:
    for line in _code_lines():
        assert not re.search(
            r"\b(cat|less|more|head|tail|grep|awk|sed|source|\.)\s+[^|]*\.env\b", line
        ), line
        assert "secrets/" not in line or line.startswith("for f in"), (
            line
        )  # the name list for the stat loop
        assert (
            "docker inspect" not in line and "docker logs" not in line and "docker exec" not in line
        )
        assert "id_rsa" not in line and ".ssh" not in line and "history" not in line, line
        assert (
            "/etc/shadow" not in line and "/proc/*/environ" not in line and "environ" not in line
        ), line
    # the only touch of the two sensitive files is a metadata-only `stat`, with a format that excludes contents
    touches = [ln for ln in _code_lines() if "memory-keyring.json" in ln or ".env" in ln]
    assert all("stat -c" in ln or "for f in" in ln for ln in touches), touches


def _run_redact(text: str, host: str = "vps-prod-01.example.net") -> str:
    fn = re.search(r"^redact\(\) \{.*?^\}", SRC, re.S | re.M)
    assert fn is not None
    short = host.split(".")[0]
    script = f'HOST_FQDN="{host}"; HOST_NAME="{short}"\n{fn.group(0)}\nredact'
    return subprocess.run(
        ["bash", "-c", script], input=text, capture_output=True, text=True, check=True
    ).stdout


def test_redaction_removes_addresses_and_host_names() -> None:
    sample = (
        "inet 203.0.113.77/24 gw 10.0.0.1\n"
        "inet6 2001:db8:85a3::8a2e:370:7334/64 and fe80::1\n"
        "link ether 52:54:00:12:34:56\n"
        "host vps-prod-01.example.net aka vps-prod-01\n"
        "listening [::]:443\n"
    )
    out = _run_redact(sample)
    for leaked in (
        "203.0.113.77",
        "10.0.0.1",
        "2001:db8",
        "fe80::1",
        "52:54:00",
        "vps-prod-01",
        "example.net",
    ):
        assert leaked not in out, f"{leaked!r} leaked: {out}"
    assert "<ipv4>" in out and "<ipv6>" in out and "<mac>" in out and "<host>" in out


def test_redaction_keeps_ordinary_values_readable() -> None:
    sample = "utc: 20261125T033717Z\nMemTotal: 16473316 kB\nmem_available_mib=15558\nkvm virtualisation: kvm\n"
    assert _run_redact(sample, host="vm").strip() == sample.strip()


def test_every_section_the_assessor_needs_is_present() -> None:
    for title in (
        "OS and kernel",
        "CPU, memory, swap, load",
        "Disk capacity",
        "Disk encryption evidence",
        "Docker and Compose",
        "Listening sockets",
        "Firewall",
        "Existing web server or reverse proxy",
        "Backup destinations",
        "Verdict inputs",
    ):
        assert f'section "{title}' in SRC, title
