#!/usr/bin/env python3
"""Operational health check for an Abu AI deployment (P1.6). Provider-agnostic: it detects and reports; WHERE an alert
goes (e-mail, chat, pager) is an owner decision and is plugged in with `--notify-cmd`.

    ABU_OPS_DATABASE_URL=postgresql://runtime-role:...@host/db \\
    python3 infrastructure/scripts/ops_check.py --api-url https://localhost:8443 --insecure-loopback \\
        --backup-dir backups --disk /var/lib/docker --disk backups --compose-project abu-ai-staging \\
        --state-file /var/lib/abu-ops/state.json --notify-cmd /usr/local/bin/send-alert

Run it every minute or two from cron or a systemd timer (examples in docs/operations/MONITORING.md). Exit code: 0 all OK,
1 at least one WARN, 2 at least one CRIT. Output is one JSON document on stdout; nothing in it is a secret.

Checks: api liveness, database reachability and migration revision, kill switch, spend freeze, authentication anomalies
(from the append-only audit log), disk usage, backup freshness / failure / off-host copy, container health.

The database URL is read from the environment (never from the command line, where it would show up in `ps`) and is never
printed; failures are reported by exception class only. The database role needs only SELECT, so the runtime role is fine.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

OK, WARN, CRIT = "OK", "WARN", "CRIT"
RANK = {OK: 0, WARN: 1, CRIT: 2}
REPO = Path(__file__).resolve().parents[2]
EXPECTED_SERVICES = ("db", "litellm", "api", "caddy")


@dataclass
class Check:
    name: str
    level: str
    message: str


def worst(checks: Sequence[Check]) -> str:
    return max((c.level for c in checks), key=lambda lvl: RANK[lvl], default=OK)


# ------------------------------------------------------------------------------------------------- individual checks
def check_api(url: str, insecure_loopback: bool = False, timeout: float = 3.0) -> Check:
    ctx = None
    host = re.sub(r"^https?://", "", url).split("/")[0].split(":")[0]
    if (
        url.startswith("https://")
        and insecure_loopback
        and host in ("localhost", "127.0.0.1", "::1")
    ):
        ctx = (
            ssl.create_default_context()
        )  # staging uses Caddy's internal CA: skip verification for LOOPBACK only
        ctx.check_hostname, ctx.verify_mode = False, ssl.CERT_NONE
    try:
        with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=timeout, context=ctx) as r:  # noqa: S310
            body = json.loads(r.read(2048))
        if r.status == 200 and body == {"status": "ok"}:
            return Check("api", OK, "liveness ok")
        return Check("api", CRIT, f"unexpected liveness response (status {r.status})")
    except (urllib.error.URLError, OSError, ValueError, TimeoutError) as exc:
        return Check("api", CRIT, f"API unreachable ({exc.__class__.__name__})")


def repo_head_revision() -> str | None:
    revs = []
    for f in (REPO / "database/migrations/versions").glob("*.py"):
        m = re.search(r'^revision\s*=\s*"([^"]+)"', f.read_text(), re.M)
        if m:
            revs.append(m.group(1))
    return max(revs) if revs else None


def _rows(conn: Any, sql: str, params: tuple[Any, ...] = ()) -> list[tuple[Any, ...]]:
    return [tuple(r) for r in conn.execute(sql, params).fetchall()]


def check_database(conn: Any, expect_revision: str | None) -> list[Check]:
    out: list[Check] = []
    try:
        rev = _rows(conn, "SELECT version_num FROM alembic_version")[0][0]
    except Exception as exc:  # noqa: BLE001
        return [Check("database", CRIT, f"query failed ({exc.__class__.__name__})")]
    out.append(Check("database", OK, f"reachable, schema revision {rev}"))
    if expect_revision and rev != expect_revision:
        out.append(
            Check(
                "schema_revision",
                CRIT,
                f"database is at {rev} but this release expects {expect_revision}",
            )
        )
    return out


def check_kill_switch(conn: Any) -> Check:
    row = _rows(
        conn, "SELECT active, activated_at, activated_via FROM kill_switch_state WHERE id = 1"
    )
    if not row:
        return Check("kill_switch", WARN, "kill switch row missing")
    active, at, via = row[0]
    if active:
        return Check(
            "kill_switch",
            WARN,
            f"kill switch is ACTIVE (since {at:%Y-%m-%d %H:%M} UTC via {via}); no new model work runs",
        )
    return Check("kill_switch", OK, "inactive")


def check_spend(conn: Any) -> Check:
    row = _rows(
        conn,
        "SELECT paid_frozen, paid_frozen_at, paid_models_enabled FROM spending_config WHERE id = 1",
    )
    if not row:
        return Check("spend", OK, "no spending configuration (paid models off by default)")
    frozen, at, enabled = row[0]
    if frozen:
        return Check(
            "spend",
            CRIT,
            f"paid spending is FROZEN since {at:%Y-%m-%d %H:%M} UTC: an operator must reconcile and unfreeze",
        )
    return Check("spend", OK, f"paid models {'ENABLED' if enabled else 'off'}, not frozen")


AUTH_RULES = (
    # (event type, warn at, crit at)  within the window
    ("enrollment.failed", 5, 20),
    ("auth.refresh_failed", 10, 40),
    ("auth.refresh_reuse_detected", 1, 3),  # a rotated refresh token came back: possible theft
)


def check_auth(conn: Any, window_minutes: int = 15, now: datetime | None = None) -> list[Check]:
    since = (now or datetime.now(UTC)) - timedelta(minutes=window_minutes)
    out = []
    for event_type, warn_at, crit_at in AUTH_RULES:
        n = _rows(
            conn,
            "SELECT count(*) FROM audit_events WHERE event_type = %s AND occurred_at >= %s",
            (event_type, since),
        )[0][0]
        level = CRIT if n >= crit_at else WARN if n >= warn_at else OK
        out.append(
            Check(
                f"auth:{event_type}",
                level,
                f"{n} in the last {window_minutes} min (warn {warn_at}, crit {crit_at})",
            )
        )
    return out


def check_disk(
    paths: Sequence[str],
    warn_pct: float = 80.0,
    crit_pct: float = 90.0,
    usage: Callable[[str], Any] = shutil.disk_usage,
) -> list[Check]:
    out = []
    for p in paths:
        try:
            u = usage(p)
        except OSError as exc:
            out.append(Check(f"disk:{p}", CRIT, f"cannot read ({exc.__class__.__name__})"))
            continue
        pct = 100.0 * u.used / u.total if u.total else 100.0
        free_gib = u.free / 2**30
        level = CRIT if pct >= crit_pct else WARN if pct >= warn_pct else OK
        out.append(
            Check(
                f"disk:{p}",
                level,
                f"{pct:.0f}% used, {free_gib:.1f} GiB free (warn {warn_pct:.0f}%, crit {crit_pct:.0f}%)",
            )
        )
    return out


def check_backup(dest: Path, max_age_hours: float, now: datetime | None = None) -> Check:
    import abu_backup

    code, msg = abu_backup.check_freshness(dest, max_age_hours, now)
    if code == 0:
        return Check("backup", OK, msg)
    if code == 1:  # stale: a warning at 1x the limit, critical at 2x
        s = json.loads((dest / abu_backup.STATUS_FILE).read_text())
        age = (now or datetime.now(UTC)) - datetime.fromisoformat(s["last_success_utc"])
        return Check("backup", CRIT if age > timedelta(hours=2 * max_age_hours) else WARN, msg)
    return Check("backup", CRIT, msg)  # failed, off-host failed, or never recorded


def check_containers(
    project: str,
    run: Callable[..., Any] = subprocess.run,
    expected: Sequence[str] = EXPECTED_SERVICES,
) -> list[Check]:
    """`docker compose ps` JSON: service, state, health. It reports no environment variables."""
    try:
        done = run(
            ["docker", "compose", "-p", project, "ps", "--all", "--format", "json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )  # noqa: S603, S607
    except (OSError, subprocess.SubprocessError) as exc:
        return [Check("containers", CRIT, f"cannot query docker ({exc.__class__.__name__})")]
    if done.returncode != 0:
        return [Check("containers", CRIT, "docker compose ps failed")]
    rows = []
    for line in done.stdout.splitlines():
        line = line.strip()
        if line.startswith("["):
            rows.extend(json.loads(line))
        elif line.startswith("{"):
            rows.append(json.loads(line))
    by_service = {r.get("Service"): r for r in rows}
    out = []
    for svc in expected:
        r = by_service.get(svc)
        if r is None:
            out.append(Check(f"container:{svc}", CRIT, "not found"))
        elif r.get("State") != "running":
            out.append(Check(f"container:{svc}", CRIT, f"state {r.get('State')}"))
        elif r.get("Health") not in ("healthy", "", None):
            out.append(Check(f"container:{svc}", CRIT, f"health {r.get('Health')}"))
        else:
            out.append(
                Check(f"container:{svc}", OK, f"running{', healthy' if r.get('Health') else ''}")
            )
    return out


# --------------------------------------------------------------------------------------------- notification on change
def decide_notification(
    prev: dict[str, Any] | None, level: str, now: datetime, repeat_minutes: int
) -> bool:
    """Notify on a state change, and re-notify a persisting CRIT every `repeat_minutes`. Never on an unchanged OK/WARN."""
    if prev is None:
        return level != OK
    if prev.get("level") != level:
        return True
    if level == CRIT:
        last = datetime.fromisoformat(prev.get("last_notified", "1970-01-01T00:00:00+00:00"))
        return now - last >= timedelta(minutes=repeat_minutes)
    return False


def notify(cmd: Sequence[str], text: str) -> bool:
    try:
        return (
            subprocess.run(  # noqa: S603 - the operator-supplied alert command, run without a shell
                list(cmd), input=text, text=True, capture_output=True, timeout=30, check=False
            ).returncode
            == 0
        )  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False


def run_checks(a: argparse.Namespace, now: datetime | None = None) -> list[Check]:
    checks: list[Check] = []
    if a.api_url:
        checks.append(check_api(a.api_url, a.insecure_loopback))
    url = os.environ.get("ABU_OPS_DATABASE_URL")
    if url:
        try:
            import psycopg

            with psycopg.connect(
                url.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=5
            ) as conn:
                checks += check_database(conn, a.expect_revision or repo_head_revision())
                for fn in (check_kill_switch, check_spend):
                    try:
                        checks.append(fn(conn))
                    except Exception as exc:  # noqa: BLE001
                        checks.append(
                            Check(
                                fn.__name__.removeprefix("check_"),
                                CRIT,
                                f"query failed ({exc.__class__.__name__})",
                            )
                        )
                try:
                    checks += check_auth(conn, a.auth_window_minutes, now)
                except Exception as exc:  # noqa: BLE001
                    checks.append(Check("auth", CRIT, f"query failed ({exc.__class__.__name__})"))
        except Exception as exc:  # noqa: BLE001 - connection errors can carry the host; only the class is reported
            checks.append(
                Check("database", CRIT, f"database unreachable ({exc.__class__.__name__})")
            )
    checks += check_disk(a.disk or [])
    if a.backup_dir:
        checks.append(check_backup(Path(a.backup_dir), a.backup_max_age_hours, now))
    if a.compose_project:
        checks += check_containers(a.compose_project)
    return checks


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--api-url")
    p.add_argument(
        "--insecure-loopback",
        action="store_true",
        help="accept the self-signed staging certificate, loopback hosts only",
    )
    p.add_argument("--expect-revision", help="default: the newest migration in this checkout")
    p.add_argument("--auth-window-minutes", type=int, default=15)
    p.add_argument(
        "--disk", action="append", help="a path whose filesystem is checked (repeatable)"
    )
    p.add_argument("--backup-dir")
    p.add_argument("--backup-max-age-hours", type=float, default=26.0)
    p.add_argument("--compose-project")
    p.add_argument("--state-file")
    p.add_argument(
        "--notify-cmd",
        help="command that receives the alert text on stdin (shell-free: split on spaces)",
    )
    p.add_argument("--repeat-minutes", type=int, default=60)
    a = p.parse_args(argv)
    now = datetime.now(UTC)
    checks = run_checks(a, now)
    level = worst(checks)
    report: dict[str, Any] = {
        "time_utc": now.isoformat(timespec="seconds"),
        "level": level,
        "checks": [asdict(c) for c in checks],
    }
    if a.state_file and a.notify_cmd:
        sf = Path(a.state_file)
        prev = json.loads(sf.read_text()) if sf.exists() else None
        if decide_notification(prev, level, now, a.repeat_minutes):
            bad = [c for c in checks if c.level != OK]
            text = f"Abu AI {level}\n" + (
                "\n".join(f"- [{c.level}] {c.name}: {c.message}" for c in bad)
                or "- recovered: all checks OK"
            )
            delivered = notify(a.notify_cmd.split(), text)
            report["notified"] = delivered
            if not delivered:
                level_for_state = (
                    prev.get("level") if prev else None
                )  # keep the old state so the next run retries
                sf.parent.mkdir(parents=True, exist_ok=True)
                sf.write_text(
                    json.dumps(
                        {
                            "level": level_for_state,
                            "last_notified": (prev or {}).get(
                                "last_notified", "1970-01-01T00:00:00+00:00"
                            ),
                        }
                    )
                )
                print(json.dumps(report, indent=2))
                return max(RANK[level], 1)
            last = now.isoformat()
        else:
            last = (prev or {}).get("last_notified", "1970-01-01T00:00:00+00:00")
        sf.parent.mkdir(parents=True, exist_ok=True)
        sf.write_text(json.dumps({"level": level, "last_notified": last}))
        sf.chmod(0o600)
    print(json.dumps(report, indent=2))
    return RANK[level]


if __name__ == "__main__":
    raise SystemExit(main())
