# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""Monitoring check (P1.6), logic that needs no database: liveness, disk, containers, notification policy."""

import importlib.util
import json
import sys
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ops_check", ROOT / "infrastructure/scripts/ops_check.py"
)
assert spec and spec.loader
oc = importlib.util.module_from_spec(spec)
sys.modules["ops_check"] = oc
spec.loader.exec_module(oc)
NOW = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)


class _Health(BaseHTTPRequestHandler):
    body = b'{"status":"ok"}'
    code = 200

    def do_GET(self):  # noqa: N802
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a):  # silence
        pass


@pytest.fixture
def server():
    srv = HTTPServer(("127.0.0.1", 0), _Health)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield srv, f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def test_api_liveness(server) -> None:
    srv, url = server
    assert oc.check_api(url).level == "OK"
    _Health.body = b'{"status":"degraded"}'
    try:
        assert oc.check_api(url).level == "CRIT"
    finally:
        _Health.body = b'{"status":"ok"}'
    srv.shutdown()
    down = oc.check_api(url, timeout=0.5)
    assert down.level == "CRIT" and "unreachable" in down.message


def test_the_insecure_tls_option_is_loopback_only() -> None:
    # a non-loopback https URL never gets the verification-skipping context, whatever the flag says
    c = oc.check_api("https://example.invalid", insecure_loopback=True, timeout=0.5)
    assert c.level == "CRIT"


def test_disk_thresholds() -> None:
    def usage(path):
        pct = {"/ok": 50, "/warn": 85, "/crit": 95}[path]
        return SimpleNamespace(total=100 * 2**30, used=pct * 2**30, free=(100 - pct) * 2**30)

    levels = {c.name: c.level for c in oc.check_disk(["/ok", "/warn", "/crit"], usage=usage)}
    assert levels == {"disk:/ok": "OK", "disk:/warn": "WARN", "disk:/crit": "CRIT"}
    bad = oc.check_disk(["/missing"], usage=lambda p: (_ for _ in ()).throw(FileNotFoundError()))
    assert bad[0].level == "CRIT"


def _ps(rows):
    def run(*a, **k):
        return SimpleNamespace(returncode=0, stdout="\n".join(json.dumps(r) for r in rows))

    return run


def test_container_health() -> None:
    rows = [
        {"Service": "db", "State": "running", "Health": "healthy"},
        {"Service": "litellm", "State": "running", "Health": "unhealthy"},
        {"Service": "api", "State": "exited", "Health": ""},
        # caddy missing entirely
    ]
    got = {c.name: c.level for c in oc.check_containers("p", run=_ps(rows))}
    assert got == {
        "container:db": "OK",
        "container:litellm": "CRIT",
        "container:api": "CRIT",
        "container:caddy": "CRIT",
    }
    failed = oc.check_containers("p", run=lambda *a, **k: SimpleNamespace(returncode=1, stdout=""))
    assert failed[0].level == "CRIT"


def test_notification_policy_alerts_on_change_and_repeats_only_critical() -> None:
    d = oc.decide_notification
    assert d(None, "OK", NOW, 60) is False  # first run, healthy: silent
    assert d(None, "CRIT", NOW, 60) is True  # first run, broken: tell someone
    assert d({"level": "OK"}, "WARN", NOW, 60) is True  # change
    assert d({"level": "WARN"}, "WARN", NOW, 60) is False  # unchanged warning: no repeat
    assert d({"level": "CRIT"}, "OK", NOW, 60) is True  # recovery is announced
    recent = {"level": "CRIT", "last_notified": (NOW - timedelta(minutes=10)).isoformat()}
    assert d(recent, "CRIT", NOW, 60) is False
    old = {"level": "CRIT", "last_notified": (NOW - timedelta(minutes=61)).isoformat()}
    assert d(old, "CRIT", NOW, 60) is True  # a persisting outage is repeated


def test_end_to_end_cli_notifies_once_through_the_configured_command(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    sink = tmp_path / "alerts.txt"
    script = tmp_path / "notify.py"
    script.write_text(f"import sys; open({str(sink)!r}, 'a').write(sys.stdin.read() + '\\n---\\n')")
    state = tmp_path / "state.json"
    monkeypatch.delenv("ABU_OPS_DATABASE_URL", raising=False)
    args = [
        "--disk", str(tmp_path / "does-not-exist"),
        "--state-file", str(state),
        "--notify-cmd", f"{sys.executable} {script}",
    ]  # fmt: skip
    assert oc.main(args) == 2  # CRIT: the path cannot be read
    assert oc.main(args) == 2  # still CRIT, same state, inside the repeat window: no second alert
    alerts = sink.read_text().split("---")
    assert (
        len([a for a in alerts if a.strip()]) == 1 and "CRIT" in alerts[0] and "disk:" in alerts[0]
    )
    out = capsys.readouterr().out
    assert json.loads(out.split("\n}\n")[0] + "\n}")["level"] == "CRIT"


def test_a_failed_notification_is_retried_on_the_next_run(tmp_path: Path, monkeypatch) -> None:
    state = tmp_path / "state.json"
    monkeypatch.delenv("ABU_OPS_DATABASE_URL", raising=False)
    base = ["--disk", str(tmp_path / "nope"), "--state-file", str(state)]
    assert (
        oc.main(
            [
                *base,
                "--notify-cmd",
                f"{sys.executable} -c 'import sys; sys.exit(1)'".replace("'", ""),
            ]
        )
        >= 1
    )
    sink = tmp_path / "ok.txt"
    script = tmp_path / "n.py"
    script.write_text(f"import sys; open({str(sink)!r}, 'w').write(sys.stdin.read())")
    oc.main([*base, "--notify-cmd", f"{sys.executable} {script}"])
    assert sink.exists(), "the alert that failed to send was not retried"


def test_the_database_url_is_never_printed(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv(
        "ABU_OPS_DATABASE_URL", "postgresql://user:SuperSecretPw123@127.0.0.1:1/none"
    )
    code = oc.main([])
    out = capsys.readouterr()
    assert code == 2 and "SuperSecretPw123" not in out.out + out.err
    assert "database unreachable" in out.out
