# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""Monitoring check against a real PostgreSQL schema (P1.6): every database-backed signal, with synthetic events."""

import importlib
import importlib.util
import sys
from argparse import Namespace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import psycopg
import pytest
from sqlalchemy.engine import make_url

from core.audit.service import ADMIN
from core.governance import killswitch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "ops_check", ROOT / "infrastructure/scripts/ops_check.py"
)
assert spec and spec.loader
oc = importlib.util.module_from_spec(spec)
sys.modules["ops_check"] = oc
spec.loader.exec_module(oc)


def _dsn(url) -> str:
    return (
        make_url(url)
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )


@pytest.fixture
def conn(migrated_url):
    with psycopg.connect(_dsn(migrated_url), autocommit=True) as c:
        yield c


def levels(checks) -> dict[str, str]:
    return {c.name: c.level for c in checks}


def args(**kw) -> Namespace:
    base = dict(
        api_url=None, insecure_loopback=False, expect_revision=None, auth_window_minutes=15,
        disk=None, backup_dir=None, backup_max_age_hours=26.0, compose_project=None,
    )  # fmt: skip
    return Namespace(**{**base, **kw})


def test_a_healthy_fresh_database_is_all_ok(conn) -> None:
    got = levels(oc.check_database(conn, oc.repo_head_revision()))
    assert got == {"database": "OK"}
    assert oc.repo_head_revision() == "0012"
    assert oc.check_kill_switch(conn).level == "OK"
    assert oc.check_spend(conn).level == "OK"
    assert set(levels(oc.check_auth(conn)).values()) == {"OK"}


def test_a_schema_revision_mismatch_is_critical(conn) -> None:
    got = levels(oc.check_database(conn, "0099"))
    assert got["schema_revision"] == "CRIT"


async def test_an_active_kill_switch_is_reported(conn, sessions) -> None:
    await killswitch.activate(sessions, ADMIN, "synthetic incident")
    c = oc.check_kill_switch(conn)
    assert c.level == "WARN" and "ACTIVE" in c.message and "synthetic incident" not in c.message
    await killswitch.deactivate(sessions, ADMIN, "synthetic all-clear")
    assert oc.check_kill_switch(conn).level == "OK"


def test_a_spend_freeze_is_critical(conn) -> None:
    conn.execute(
        "INSERT INTO spending_config (id, paid_frozen, paid_frozen_at, paid_frozen_reason) "
        "VALUES (1, true, now(), 'synthetic unknown cost') "
        "ON CONFLICT (id) DO UPDATE SET paid_frozen = true, paid_frozen_at = now(), paid_frozen_reason = 'synthetic unknown cost'"
    )
    c = oc.check_spend(conn)
    assert c.level == "CRIT" and "FROZEN" in c.message
    assert "synthetic unknown cost" not in c.message  # operator free text stays out of alerts


def _event(conn, event_type: str, n: int, minutes_ago: float = 1.0) -> None:
    for _ in range(n):
        conn.execute(
            "INSERT INTO audit_events (occurred_at, actor_type, event_type, subject_type, subject_id, detail) "
            "VALUES (now() - (%s * interval '1 minute'), 'system', %s, 'x', 'y', '{}'::jsonb)",
            (minutes_ago, event_type),
        )


def test_authentication_anomalies_are_graded_from_the_audit_log(conn) -> None:
    _event(conn, "enrollment.failed", 6)  # warn at 5
    _event(conn, "auth.refresh_failed", 40)  # crit at 40
    _event(conn, "auth.refresh_reuse_detected", 1)  # crit-class signal; warn at 1
    got = levels(oc.check_auth(conn))
    assert got["auth:enrollment.failed"] == "WARN"
    assert got["auth:auth.refresh_failed"] == "CRIT"
    assert got["auth:auth.refresh_reuse_detected"] == "WARN"
    _event(conn, "auth.refresh_reuse_detected", 2)
    assert levels(oc.check_auth(conn))["auth:auth.refresh_reuse_detected"] == "CRIT"


def test_old_authentication_events_age_out_of_the_window(conn) -> None:
    _event(conn, "enrollment.failed", 50, minutes_ago=120)
    assert levels(oc.check_auth(conn, window_minutes=15))["auth:enrollment.failed"] == "OK"


def test_the_whole_run_uses_only_the_environment_for_the_database_url(
    migrated_url, monkeypatch, tmp_path: Path, conn
) -> None:
    monkeypatch.setenv(
        "ABU_OPS_DATABASE_URL", make_url(migrated_url).render_as_string(hide_password=False)
    )
    _event(conn, "auth.refresh_reuse_detected", 3)
    checks = oc.run_checks(args(disk=[str(tmp_path)], backup_dir=str(tmp_path / "no-backups")))
    got = levels(checks)
    assert got["database"] == "OK" and got["auth:auth.refresh_reuse_detected"] == "CRIT"
    assert got["backup"] == "CRIT"  # nothing was ever backed up here
    assert oc.worst(checks) == "CRIT"


def test_backup_signal_follows_the_status_file(tmp_path: Path) -> None:
    abu_backup = importlib.import_module("abu_backup")

    now = datetime(2026, 11, 25, 12, 0, tzinfo=UTC)
    assert oc.check_backup(tmp_path, 26, now).level == "CRIT"  # never recorded
    abu_backup.write_status(
        tmp_path,
        last_result="ok",
        last_success_utc=(now - timedelta(hours=3)).isoformat(),
        last_attempt_utc=now.isoformat(),
    )
    assert oc.check_backup(tmp_path, 26, now).level == "OK"
    abu_backup.write_status(tmp_path, last_success_utc=(now - timedelta(hours=30)).isoformat())
    assert oc.check_backup(tmp_path, 26, now).level == "WARN"  # stale
    abu_backup.write_status(tmp_path, last_success_utc=(now - timedelta(hours=60)).isoformat())
    assert oc.check_backup(tmp_path, 26, now).level == "CRIT"  # very stale
    abu_backup.write_status(
        tmp_path, last_success_utc=now.isoformat(), last_result="failed", last_error="dump failed"
    )
    assert oc.check_backup(tmp_path, 26, now).level == "CRIT"  # last attempt failed
    abu_backup.write_status(tmp_path, last_result="ok", offhost_ok=False)
    assert (
        oc.check_backup(tmp_path, 26, now).level == "CRIT"
    )  # local copy fine, off-host copy failed


def test_a_database_that_cannot_be_reached_is_critical_and_leaks_nothing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ABU_OPS_DATABASE_URL", "postgresql://u:NeverShownPw42@127.0.0.1:1/none")
    checks = oc.run_checks(args())
    assert levels(checks)["database"] == "CRIT"
    assert "NeverShownPw42" not in str([c.message for c in checks])


def test_the_runtime_role_is_enough_for_every_query(
    migrated_url, tmp_path: Path, monkeypatch
) -> None:
    """The check needs SELECT only: run it as a role that has exactly that."""
    admin = make_url(migrated_url)
    role = "abu_ops_ro_" + tmp_path.name[-6:].replace("-", "_").lower()
    with psycopg.connect(_dsn(admin), autocommit=True) as c:
        c.execute(f"CREATE ROLE {role} LOGIN PASSWORD 'ro-test-pw'")  # noqa: S608
        c.execute(f"GRANT SELECT ON ALL TABLES IN SCHEMA public TO {role}")
        try:
            ro = admin.set(username=role, password="ro-test-pw")  # noqa: S106
            ro_url = ro.render_as_string(hide_password=False)
            monkeypatch.setenv("ABU_OPS_DATABASE_URL", ro_url)
            got = levels(oc.run_checks(args()))
            assert got["database"] == "OK" and got["kill_switch"] == "OK" and got["spend"] == "OK"
            assert all(v == "OK" for k, v in got.items() if k.startswith("auth:"))
        finally:
            c.execute(f"REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {role}")
            c.execute(f"DROP ROLE {role}")
