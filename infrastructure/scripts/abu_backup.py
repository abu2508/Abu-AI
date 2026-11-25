#!/usr/bin/env python3
"""Backup orchestration for Abu AI: encrypted database dumps, integrity verification, retention, a status signal for
monitoring, an INDEPENDENT keyring backup, and a recovery check that proves a restored database can be read again.

Everything is built on `encrypted_backup.py` (authenticated, chunked AES-256-GCM; any bit flip, reordering or
truncation is detected). Nothing here prints, logs or stores a key, a password or decrypted content.

    python3 infrastructure/scripts/abu_backup.py backup          --key-file K1 --dest DIR [--offhost-cmd ...]
    python3 infrastructure/scripts/abu_backup.py verify          --key-file K1 FILE
    python3 infrastructure/scripts/abu_backup.py prune           --dest DIR [--apply]
    python3 infrastructure/scripts/abu_backup.py status          --dest DIR --max-age-hours 26
    python3 infrastructure/scripts/abu_backup.py keyring-backup  --keyring secrets/memory-keyring.json --key-file K2 \\
                                                                 --db-key-file K1 --dest DIR
    python3 infrastructure/scripts/abu_backup.py keyring-restore --key-file K2 FILE --out NEW-keyring.json
    uv run python infrastructure/scripts/abu_backup.py recovery-check --database-url DSN --keyring NEW-keyring.json

Design rules (docs/operations/BACKUP_RESTORE.md):
  * a backup is written to `<name>.partial` and renamed only when complete; an existing backup is NEVER overwritten;
  * a backup is verified right after it is written (manifest checksum, authenticated decryption, archive table of
    contents) and the result goes into `status.json`, which the monitoring check reads;
  * retention only ever deletes files that match this tool's own naming pattern, never the newest backup, and is a
    dry run unless `--apply` is given;
  * the database backup key (K1) and the keyring backup key (K2) must differ, so no single secret recovers both.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import BinaryIO, cast

sys.path.insert(0, str(Path(__file__).resolve().parent))
import encrypted_backup as eb  # noqa: E402  (sibling module, tested separately)

PREFIX = "abu-ai"
DB_RE = re.compile(rf"^{PREFIX}-(\d{{8}}T\d{{6}}Z)\.dump\.enc$")
KR_RE = re.compile(r"^abu-keyring-(\d{8}T\d{6}Z)\.enc$")
STATUS_FILE = "status.json"
DEFAULT_DUMP = [
    "docker",
    "compose",
    "--env-file",
    ".env",
    "-f",
    "infrastructure/docker-compose.yml",
    "exec",
    "-T",
    "db",
    "sh",
    "-c",
    'pg_dump -Fc -U "$POSTGRES_USER" "$APP_DB_NAME"',
]
DEFAULT_LIST = ["pg_restore", "--list"]
# Tables a real Abu AI archive must contain: a backup of the wrong database (or an empty one) fails verification.
EXPECTED_TABLES = ("users", "alembic_version", "memory_records")


class BackupToolError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(UTC)


def stamp(t: datetime) -> str:
    return t.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def parse_stamp(s: str) -> datetime:
    return datetime.strptime(s, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)


def _write_manifest(path: Path) -> Path:
    manifest = path.with_name(path.name + ".sha256")
    fd = os.open(manifest, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(f"{sha256_file(path)}  {path.name}\n")
    return manifest


def check_manifest(path: Path) -> None:
    manifest = path.with_name(path.name + ".sha256")
    if not manifest.exists():
        raise BackupToolError("manifest (.sha256) is missing")
    expected = manifest.read_text().split()[0]
    if sha256_file(path) != expected:
        raise BackupToolError("checksum mismatch: the backup file changed after it was written")


# ----------------------------------------------------------------------------------------------- status signal
def write_status(dest: Path, **fields: object) -> None:
    """Atomically merge fields into status.json (the file the monitoring check reads). Never holds a secret."""
    current: dict[str, object] = {}
    p = dest / STATUS_FILE
    if p.exists():
        try:
            current = json.loads(p.read_text())
        except ValueError:
            current = {}
    current.update(fields)
    fd, tmp = tempfile.mkstemp(dir=dest, prefix=".status-")
    with os.fdopen(fd, "w") as fh:
        json.dump(current, fh, indent=2, sort_keys=True)
    os.chmod(tmp, 0o600)
    os.replace(tmp, p)


def check_freshness(
    dest: Path, max_age_hours: float, now: datetime | None = None
) -> tuple[int, str]:
    """Exit codes for an external monitor: 0 fresh and healthy, 1 stale, 2 last backup failed, 3 no status at all."""
    now = now or _now()
    p = dest / STATUS_FILE
    if not p.exists():
        return 3, "no backup has ever been recorded here"
    s = json.loads(p.read_text())
    if s.get("last_result") == "failed":
        return (
            2,
            f"the last backup attempt FAILED at {s.get('last_attempt_utc')}: {s.get('last_error', 'unknown')}",
        )
    ok = s.get("last_success_utc")
    if not ok:
        return 3, "no successful backup is recorded"
    age = now - datetime.fromisoformat(str(ok))
    if age > timedelta(hours=max_age_hours):
        return (
            1,
            f"the newest successful backup is {age.total_seconds() / 3600:.1f} h old (limit {max_age_hours} h)",
        )
    if s.get("offhost_ok") is False:
        return 2, "the off-host copy of the last backup FAILED"
    return 0, f"ok: last success {ok}, {age.total_seconds() / 3600:.1f} h ago"


# -------------------------------------------------------------------------------------------------- verification
def verify_backup(
    path: Path,
    key_file: str,
    list_argv: Sequence[str] = DEFAULT_LIST,
    expect_tables: Sequence[str] = EXPECTED_TABLES,
) -> int:
    """Checksum, then authenticated decryption streamed into `pg_restore --list` (reads the archive's table of
    contents; touches no database). Returns the number of TOC entries. Raises BackupToolError on any problem."""
    check_manifest(path)
    key = eb.load_key(key_file)
    proc = subprocess.Popen(  # noqa: S603
        list(list_argv), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
    )
    if proc.stdin is None or proc.stdout is None:
        raise BackupToolError("could not open the archive reader")
    try:
        with path.open("rb") as src:
            try:
                eb.decrypt_stream(src, cast(BinaryIO, proc.stdin), key)
            except eb.BackupError as exc:
                proc.kill()
                raise BackupToolError(f"decryption failed: {exc}") from exc
            except BrokenPipeError:
                raise BackupToolError("archive reader stopped early (not a valid dump)") from None
        proc.stdin.close()
        toc = proc.stdout.read().decode(errors="replace")
    finally:
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode != 0:
        raise BackupToolError("the decrypted data is not a valid PostgreSQL archive")
    missing = [t for t in expect_tables if not re.search(rf"\bTABLE\b.*\b{re.escape(t)}\b", toc)]
    if missing:
        raise BackupToolError(f"archive does not contain expected tables: {', '.join(missing)}")
    return sum(1 for ln in toc.splitlines() if ln and not ln.startswith(";"))


# ------------------------------------------------------------------------------------------------------- backup
@dataclass
class BackupResult:
    path: Path
    bytes: int
    sha256: str
    toc_entries: int
    offhost_ok: bool | None = None
    notes: list[str] = field(default_factory=list)


def run_backup(
    dump_argv: Sequence[str],
    key_file: str,
    dest: Path,
    *,
    list_argv: Sequence[str] = DEFAULT_LIST,
    offhost_argv: Sequence[str] | None = None,
    now: datetime | None = None,
    expect_tables: Sequence[str] = EXPECTED_TABLES,
) -> BackupResult:
    now = now or _now()
    _private_dir(dest)
    key = eb.load_key(key_file)
    final = dest / f"{PREFIX}-{stamp(now)}.dump.enc"
    partial = final.with_name(final.name + ".partial")
    attempt = now.isoformat()
    written = False
    try:
        if final.exists():
            raise BackupToolError(f"{final.name} already exists: backups are never overwritten")
        proc = subprocess.Popen(  # noqa: S603
            list(dump_argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        )
        if proc.stdout is None:
            raise BackupToolError("could not open the dump stream")
        fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as out:
            eb.encrypt_stream(cast(BinaryIO, proc.stdout), out, key)
            out.flush()
            os.fsync(out.fileno())
        if proc.wait() != 0:
            raise BackupToolError(f"the dump command failed (exit status {proc.returncode})")
        if partial.stat().st_size < 64:
            raise BackupToolError("the dump produced no data")
        os.replace(partial, final)
        written = True
        _write_manifest(final)
        toc = verify_backup(final, key_file, list_argv, expect_tables)
    except (BackupToolError, eb.BackupError, OSError) as exc:
        partial.unlink(missing_ok=True)
        if written and final.exists():
            # Written but not proven good: it must not look like a usable backup, and it is not deleted either.
            final.rename(final.with_name(final.name + ".unverified"))
            final.with_name(final.name + ".sha256").unlink(missing_ok=True)
        write_status(
            dest, last_attempt_utc=attempt, last_result="failed", last_error=str(exc)[:200]
        )
        raise BackupToolError(str(exc)) from exc
    result = BackupResult(final, final.stat().st_size, sha256_file(final), toc)
    if offhost_argv:
        argv = [
            a.replace("{file}", str(final)).replace("{manifest}", str(final) + ".sha256")
            for a in offhost_argv
        ]
        ok = subprocess.run(argv, check=False, capture_output=True).returncode == 0  # noqa: S603
        result.offhost_ok = ok
        if not ok:
            result.notes.append("off-host copy FAILED")
    write_status(
        dest,
        last_attempt_utc=attempt,
        last_result="ok",
        last_error=None,
        last_success_utc=now.isoformat(),
        last_file=final.name,
        last_bytes=result.bytes,
        last_sha256=result.sha256,
        last_verified=True,
        last_toc_entries=toc,
        offhost_ok=result.offhost_ok,
    )
    return result


# ----------------------------------------------------------------------------------------------------- retention
@dataclass(frozen=True)
class Policy:
    keep_daily: int = 14  # the newest backup of each of the last N days
    keep_weekly: int = 8  # the newest of each of the last N ISO weeks
    keep_monthly: int = 6  # the newest of each of the last N months


def plan_prune(
    files: Sequence[Path], policy: Policy, now: datetime | None = None
) -> tuple[list[Path], list[Path]]:
    """(keep, delete). Only names matching the tool's own pattern are considered; the newest is always kept."""
    now = now or _now()
    dated = sorted(
        ((parse_stamp(m.group(1)), f) for f in files if (m := DB_RE.match(f.name))), reverse=True
    )
    if not dated:
        return [], []
    keep: set[Path] = {dated[0][1]}
    seen_day: set[str] = set()
    seen_week: set[tuple[int, int]] = set()
    seen_month: set[tuple[int, int]] = set()
    for (
        t,
        f,
    ) in dated:  # newest first, so the first file seen in a bucket is the newest of that bucket
        day = t.strftime("%Y-%m-%d")
        iso = t.isocalendar()
        week, month = (iso.year, iso.week), (t.year, t.month)
        if day not in seen_day and (now - t).days < policy.keep_daily:
            keep.add(f)
        if week not in seen_week and (now - t).days < policy.keep_weekly * 7:
            keep.add(f)
        if month not in seen_month and (now - t).days < policy.keep_monthly * 31:
            keep.add(f)
        seen_day.add(day)
        seen_week.add(week)
        seen_month.add(month)
    return sorted(keep), sorted(f for _, f in dated if f not in keep)


def prune(
    dest: Path, policy: Policy, apply: bool, now: datetime | None = None
) -> tuple[list[Path], list[Path]]:
    keep, drop = plan_prune([p for p in dest.iterdir() if p.is_file()], policy, now)
    if apply:
        for f in drop:
            f.unlink()
            f.with_name(f.name + ".sha256").unlink(missing_ok=True)
    return keep, drop


# --------------------------------------------------------------------------------------------- keyring (G2 input)
def _keyring_ids(path: str, strict: bool) -> list[str]:
    from core.memory.crypto import (
        KeyRing,
    )  # imported lazily: the dump/verify/prune path needs only `cryptography`

    return sorted(KeyRing.from_file(path, strict_permissions=strict).keys)


def backup_keyring(
    keyring_file: str, key_file: str, db_key_file: str, dest: Path, now: datetime | None = None
) -> tuple[Path, list[str]]:
    """Encrypt the memory keyring with K2 (a key DIFFERENT from the database backup key K1)."""
    if eb.load_key(key_file) == eb.load_key(db_key_file):
        raise BackupToolError("the keyring backup key must differ from the database backup key")
    ids = _keyring_ids(keyring_file, strict=False)  # validates structure; never prints key material
    _private_dir(dest)
    out = dest / f"abu-keyring-{stamp(now or _now())}.enc"
    if out.exists():
        raise BackupToolError(f"{out.name} already exists: backups are never overwritten")
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with Path(keyring_file).open("rb") as src, os.fdopen(fd, "wb") as dst:
        eb.encrypt_stream(src, dst, eb.load_key(key_file))
    _write_manifest(out)
    return out, ids


def restore_keyring(backup: Path, key_file: str, out_path: Path) -> list[str]:
    """Decrypt a keyring backup to a NEW mode-600 file and validate it. Never overwrites, never prints key material."""
    check_manifest(backup)
    try:
        fd = os.open(out_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        raise BackupToolError(f"{out_path.name} already exists: it is never overwritten") from None
    try:
        with backup.open("rb") as src, os.fdopen(fd, "wb") as dst:
            eb.decrypt_stream(src, dst, eb.load_key(key_file))
        return _keyring_ids(str(out_path), strict=True)
    except Exception as exc:
        out_path.unlink(missing_ok=True)
        raise BackupToolError(f"keyring could not be recovered: {exc.__class__.__name__}") from None


# ------------------------------------------------------------------------------------------- recovery check (G2)
def recovery_check(database_url: str, keyring_file: str, sample: int = 25) -> dict[str, object]:
    """Open real encrypted rows of a (restored) database with a (recovered) keyring. Reports counts and key ids only."""
    import psycopg

    from core.memory.crypto import Envelope, KeyRing, LocalKeyProvider, aad_for

    env = Envelope(LocalKeyProvider(KeyRing.from_file(keyring_file, strict_permissions=False)))
    out: dict[str, object] = {"key_ids": _keyring_ids(keyring_file, strict=False)}
    with psycopg.connect(database_url.replace("postgresql+psycopg://", "postgresql://", 1)) as c:
        checks = {
            "memory_records": (
                "SELECT user_id, id, content_enc FROM memory_records WHERE content_enc IS NOT NULL LIMIT %s",
                "content",
            ),
            "finance_transactions": (
                "SELECT user_id, id, description_enc FROM fin_transactions WHERE description_enc IS NOT NULL LIMIT %s",
                "fin.txn.desc",
            ),
        }
        for name, (query, purpose) in checks.items():
            ok = bad = 0
            for user_id, row_id, blob in c.execute(query, (sample,)).fetchall():
                try:
                    env.open(bytes(blob), aad_for(user_id, row_id, purpose))
                    ok += 1
                except Exception:  # noqa: BLE001 - any failure to decrypt counts; the reason is deliberately not shown
                    bad += 1
            out[name] = {"decrypted": ok, "failed": bad}
    return out


# ------------------------------------------------------------------------------------------------------------ CLI
def _policy(a: argparse.Namespace) -> Policy:
    return Policy(a.keep_daily, a.keep_weekly, a.keep_monthly)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backup")
    b.add_argument("--key-file", default=os.environ.get("ABU_BACKUP_KEY_FILE"))
    b.add_argument("--dest", default=os.environ.get("ABU_BACKUP_DIR", "backups"))
    b.add_argument(
        "--dump-cmd",
        nargs=argparse.REMAINDER,
        help="override the dump command (everything after this flag)",
    )
    b.add_argument(
        "--offhost-cmd",
        default=os.environ.get("ABU_BACKUP_OFFHOST_COMMAND"),
        help="shell-free template with {file} and {manifest}",
    )
    v = sub.add_parser("verify")
    v.add_argument("--key-file", default=os.environ.get("ABU_BACKUP_KEY_FILE"))
    v.add_argument("file")
    pr = sub.add_parser("prune")
    pr.add_argument("--dest", default=os.environ.get("ABU_BACKUP_DIR", "backups"))
    pr.add_argument("--apply", action="store_true")
    for name, default in (("keep-daily", 14), ("keep-weekly", 8), ("keep-monthly", 6)):
        pr.add_argument(f"--{name}", type=int, default=default)
    st = sub.add_parser("status")
    st.add_argument("--dest", default=os.environ.get("ABU_BACKUP_DIR", "backups"))
    st.add_argument("--max-age-hours", type=float, default=26.0)
    kb = sub.add_parser("keyring-backup")
    kb.add_argument("--keyring", required=True)
    kb.add_argument(
        "--key-file", required=True, help="K2: must differ from the database backup key"
    )
    kb.add_argument(
        "--db-key-file",
        required=True,
        help="K1, only to prove K2 differs; it is not used to encrypt",
    )
    kb.add_argument("--dest", required=True)
    kr = sub.add_parser("keyring-restore")
    kr.add_argument("--key-file", required=True)
    kr.add_argument("--out", required=True)
    kr.add_argument("file")
    rc = sub.add_parser("recovery-check")
    rc.add_argument("--database-url", required=True)
    rc.add_argument("--keyring", required=True)
    rc.add_argument("--sample", type=int, default=25)
    a = p.parse_args(argv)
    try:
        if a.cmd == "backup":
            if not a.key_file:
                raise BackupToolError("--key-file (or ABU_BACKUP_KEY_FILE) is required")
            res = run_backup(
                a.dump_cmd or DEFAULT_DUMP,
                a.key_file,
                Path(a.dest),
                offhost_argv=a.offhost_cmd.split() if a.offhost_cmd else None,
            )
            print(
                f"backup ok: {res.path.name} ({res.bytes} bytes, {res.toc_entries} archive entries, verified)"
            )
            if res.notes:
                print("; ".join(res.notes), file=sys.stderr)
                return 2
        elif a.cmd == "verify":
            n = verify_backup(Path(a.file), a.key_file)
            print(
                f"verified: checksum ok, authenticated decryption ok, valid archive ({n} entries)"
            )
        elif a.cmd == "prune":
            keep, drop = prune(Path(a.dest), _policy(a), a.apply)
            verb = "deleted" if a.apply else "would delete (dry run; use --apply)"
            print(f"keep {len(keep)}; {verb} {len(drop)}")
            for f in drop:
                print(f"  {f.name}")
        elif a.cmd == "status":
            code, msg = check_freshness(Path(a.dest), a.max_age_hours)
            print(msg)
            return code
        elif a.cmd == "keyring-backup":
            out, ids = backup_keyring(a.keyring, a.key_file, a.db_key_file, Path(a.dest))
            print(
                f"keyring backed up: {out.name} (key ids: {', '.join(ids)}). Store the K2 key file OFFLINE and apart from K1."
            )
        elif a.cmd == "keyring-restore":
            ids = restore_keyring(Path(a.file), a.key_file, Path(a.out))
            print(f"keyring recovered to {a.out} (key ids: {', '.join(ids)})")
        elif a.cmd == "recovery-check":
            report = recovery_check(a.database_url, a.keyring, a.sample)
            print(json.dumps(report, sort_keys=True))
            counts = [v for v in report.values() if isinstance(v, dict)]
            if any(c["failed"] for c in counts):
                return 3
            if not any(c["decrypted"] for c in counts):
                print(
                    "nothing encrypted was found to check: recovery is NOT demonstrated",
                    file=sys.stderr,
                )
                return 4
    except (BackupToolError, eb.BackupError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
