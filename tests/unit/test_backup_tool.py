# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""Backup orchestration (P1.5): encrypted dumps that are verified, never overwritten, retained by policy, reported to
monitoring, and a keyring backup that is independent of the database backup. No database needed here; a stub stands in
for pg_dump / pg_restore. The real thing is rehearsed in tests/integration/test_backup_restore_rehearsal.py."""

import base64
import importlib.util
import json
import os
import stat
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "abu_backup", ROOT / "infrastructure/scripts/abu_backup.py"
)
assert spec and spec.loader
ab = importlib.util.module_from_spec(spec)
sys.modules["abu_backup"] = ab
spec.loader.exec_module(ab)
eb = ab.eb

PAYLOAD = b"PGDMP" + b"synthetic-dump-body-" * 400
DUMP = [sys.executable, "-c", f"import sys; sys.stdout.buffer.write({PAYLOAD!r})"]
FAILING_DUMP = [
    sys.executable,
    "-c",
    "import sys; sys.stdout.buffer.write(b'PGDMP' + b'x' * 300); sys.exit(1)",
]
EMPTY_DUMP = [sys.executable, "-c", "pass"]
# stands in for `pg_restore --list`: exits non-zero unless the input looks like an archive, prints a table of contents
LIST = [
    sys.executable,
    "-c",
    "import sys\n"
    "d = sys.stdin.buffer.read()\n"
    "sys.exit(0 if d.startswith(b'PGDMP') else 1) if d.startswith(b'PGDMP') is False else None\n"
    "print('; Archive created')\n"
    "for t in ('users', 'alembic_version', 'memory_records'):\n"
    "    print(f'200; 1259 16401 TABLE public {t} owner')\n",
]
NOW = datetime(2026, 11, 25, 4, 0, 0, tzinfo=UTC)


@pytest.fixture
def keys(tmp_path: Path) -> tuple[str, str]:
    k1, k2 = str(tmp_path / "k1.key"), str(tmp_path / "k2.key")
    eb.genkey(k1)
    eb.genkey(k2)
    return k1, k2


def run(dest: Path, key: str, now: datetime = NOW, dump: list[str] = DUMP, **kw: object):
    return ab.run_backup(dump, key, dest, list_argv=LIST, now=now, **kw)


def decrypt(path: Path, key: str) -> bytes:
    import io

    out = io.BytesIO()
    with path.open("rb") as src:
        eb.decrypt_stream(src, out, eb.load_key(key))
    return out.getvalue()


# ------------------------------------------------------------------------------------------------- backup
def test_a_backup_is_encrypted_private_checksummed_verified_and_reported(
    tmp_path: Path, keys
) -> None:
    dest = tmp_path / "b"
    r = run(dest, keys[0])
    assert r.path.name == "abu-ai-20261125T040000Z.dump.enc"
    assert (
        stat.S_IMODE(r.path.stat().st_mode) == 0o600 and stat.S_IMODE(dest.stat().st_mode) == 0o700
    )
    raw = r.path.read_bytes()
    assert b"synthetic-dump-body" not in raw and not raw.startswith(
        b"PGDMP"
    )  # ciphertext, not the dump
    assert decrypt(r.path, keys[0]) == PAYLOAD  # and it round-trips
    manifest = r.path.with_name(r.path.name + ".sha256").read_text().split()
    assert manifest == [ab.sha256_file(r.path), r.path.name]
    s = json.loads((dest / "status.json").read_text())
    assert s["last_result"] == "ok" and s["last_verified"] is True and s["last_file"] == r.path.name
    assert not list(dest.glob("*.partial"))


def test_an_existing_backup_is_never_overwritten(tmp_path: Path, keys) -> None:
    dest = tmp_path / "b"
    first = run(dest, keys[0]).path
    before = (first.read_bytes(), first.stat().st_mtime_ns)
    with pytest.raises(ab.BackupToolError, match="never overwritten"):
        run(dest, keys[0])  # same second => same name
    assert (first.read_bytes(), first.stat().st_mtime_ns) == before


@pytest.mark.parametrize("dump", [FAILING_DUMP, EMPTY_DUMP])
def test_a_failed_or_empty_dump_leaves_no_backup_and_raises_the_alarm(
    tmp_path: Path, keys, dump
) -> None:
    dest = tmp_path / "b"
    with pytest.raises(ab.BackupToolError):
        run(dest, keys[0], dump=dump)
    assert not [p for p in dest.iterdir() if p.name != "status.json"], (
        "no partial or final file may remain"
    )
    code, msg = ab.check_freshness(dest, 26, NOW)
    assert code == 2 and "FAILED" in msg


def test_a_dump_that_is_not_a_valid_archive_is_not_kept_as_a_backup(tmp_path: Path, keys) -> None:
    dest = tmp_path / "b"
    bad = [sys.executable, "-c", "import sys; sys.stdout.buffer.write(b'NOTPG' + b'x' * 300)"]
    with pytest.raises(ab.BackupToolError):
        run(dest, keys[0], dump=bad)
    names = sorted(p.name for p in dest.iterdir())
    assert not any(
        n.endswith(".dump.enc") for n in names
    )  # nothing that looks like a usable backup
    assert any(n.endswith(".unverified") for n in names)  # kept for inspection, clearly marked
    assert ab.check_freshness(dest, 26, NOW)[0] == 2


def test_a_backup_of_the_wrong_database_fails_verification(tmp_path: Path, keys) -> None:
    dest = tmp_path / "b"
    with pytest.raises(ab.BackupToolError, match="expected tables"):
        run(dest, keys[0], expect_tables=("users", "table_that_is_not_there"))


# ---------------------------------------------------------------------------------------------- verification
def test_verification_detects_tampering_truncation_wrong_key_and_checksum_drift(
    tmp_path: Path, keys
) -> None:
    f = run(tmp_path / "b", keys[0]).path
    assert ab.verify_backup(f, keys[0], LIST) >= 3
    with pytest.raises(ab.BackupToolError, match="decryption failed"):
        ab.verify_backup(f, keys[1], LIST)  # wrong key
    good = f.read_bytes()
    manifest = f.with_name(f.name + ".sha256")
    flipped = bytearray(good)
    flipped[len(good) // 2] ^= 0x01
    f.write_bytes(bytes(flipped))
    with pytest.raises(ab.BackupToolError, match="checksum mismatch"):
        ab.verify_backup(f, keys[0], LIST)  # the manifest catches it first ...
    manifest.write_text(f"{ab.sha256_file(f)}  {f.name}\n")  # ... and even with a forged manifest,
    with pytest.raises(ab.BackupToolError, match="decryption failed"):
        ab.verify_backup(f, keys[0], LIST)  # authenticated encryption catches it
    f.write_bytes(good[: len(good) // 2])
    manifest.write_text(f"{ab.sha256_file(f)}  {f.name}\n")
    with pytest.raises(ab.BackupToolError, match="decryption failed"):
        ab.verify_backup(f, keys[0], LIST)  # truncation
    f.write_bytes(good)
    manifest.unlink()
    with pytest.raises(ab.BackupToolError, match="manifest"):
        ab.verify_backup(f, keys[0], LIST)


# ------------------------------------------------------------------------------------------------ retention
def _make(dest: Path, when: datetime) -> Path:
    p = dest / f"abu-ai-{ab.stamp(when)}.dump.enc"
    p.write_bytes(b"x")
    p.with_name(p.name + ".sha256").write_text("x")
    return p


def test_retention_keeps_daily_weekly_monthly_and_always_the_newest(tmp_path: Path) -> None:
    dest = tmp_path
    files = [
        _make(dest, NOW - timedelta(days=d, hours=1))
        for d in list(range(0, 40)) + [60, 90, 120, 150, 200, 400]
    ]
    keep, drop = ab.plan_prune(files, ab.Policy(7, 4, 3), NOW)
    kept_age = sorted((NOW - ab.parse_stamp(ab.DB_RE.match(p.name).group(1))).days for p in keep)
    assert kept_age[0] == 0  # the newest is always kept
    assert all(d in kept_age for d in range(0, 7))  # the last 7 days are all there
    assert 400 not in kept_age and 200 not in kept_age  # ancient ones go
    assert set(keep).isdisjoint(drop) and len(keep) + len(drop) == len(files)
    assert (
        len([d for d in kept_age if 7 <= d < 40]) <= 6
    )  # then roughly one per week/month, not everything


def test_retention_is_a_dry_run_by_default_and_touches_only_its_own_files(tmp_path: Path) -> None:
    old = _make(tmp_path, NOW - timedelta(days=300))
    _make(tmp_path, NOW)
    stranger = tmp_path / "important-notes.txt"
    lookalike = tmp_path / "abu-ai-latest.dump.enc"
    keyring = tmp_path / "abu-keyring-20200101T000000Z.enc"
    for f in (stranger, lookalike, keyring):
        f.write_text("keep me")
    keep, drop = ab.prune(tmp_path, ab.Policy(3, 2, 1), apply=False, now=NOW)
    assert old in drop and old.exists()  # dry run deleted nothing
    ab.prune(tmp_path, ab.Policy(3, 2, 1), apply=True, now=NOW)
    assert not old.exists() and not old.with_name(old.name + ".sha256").exists()
    assert stranger.exists() and lookalike.exists() and keyring.exists()


def test_retention_never_empties_the_directory(tmp_path: Path) -> None:
    only = _make(tmp_path, NOW - timedelta(days=900))
    keep, drop = ab.prune(tmp_path, ab.Policy(1, 1, 1), apply=True, now=NOW)
    assert keep == [only] and drop == [] and only.exists()


# ------------------------------------------------------------------------------------------------ monitoring
def test_freshness_signal(tmp_path: Path, keys) -> None:
    assert ab.check_freshness(tmp_path, 26, NOW)[0] == 3  # nothing recorded
    run(tmp_path, keys[0])
    assert ab.check_freshness(tmp_path, 26, NOW + timedelta(hours=2))[0] == 0
    code, msg = ab.check_freshness(tmp_path, 26, NOW + timedelta(hours=30))
    assert code == 1 and "30.0 h" in msg  # stale
    with pytest.raises(ab.BackupToolError):
        run(tmp_path, keys[0], now=NOW + timedelta(hours=1), dump=FAILING_DUMP)
    assert (
        ab.check_freshness(tmp_path, 26, NOW + timedelta(hours=2))[0] == 2
    )  # the latest attempt failed


def test_an_offhost_failure_is_reported_even_though_the_local_backup_is_good(
    tmp_path: Path, keys
) -> None:
    r = run(
        tmp_path, keys[0], offhost_argv=[sys.executable, "-c", "import sys; sys.exit(7)", "{file}"]
    )
    assert r.offhost_ok is False and r.path.exists()
    assert ab.check_freshness(tmp_path, 26, NOW)[0] == 2
    ok = run(
        tmp_path,
        keys[0],
        now=NOW + timedelta(hours=1),
        offhost_argv=[sys.executable, "-c", "pass", "{file}", "{manifest}"],
    )
    assert (
        ok.offhost_ok is True and ab.check_freshness(tmp_path, 26, NOW + timedelta(hours=1))[0] == 0
    )


# ------------------------------------------------------------------------------------------- keyring backup
def _keyring(path: Path, key_ids: tuple[str, ...] = ("k1",)) -> str:
    data = {
        "active": key_ids[0],
        "keys": {k: base64.b64encode(os.urandom(32)).decode() for k in key_ids},
    }
    path.write_text(json.dumps(data))
    path.chmod(0o600)
    return str(path)


def test_the_keyring_backup_is_independent_of_the_database_backup_key(
    tmp_path: Path, keys, capsys
) -> None:
    kr = _keyring(tmp_path / "memory-keyring.json", ("k1", "k2"))
    secret_b64 = json.loads(Path(kr).read_text())["keys"]["k1"]
    with pytest.raises(ab.BackupToolError, match="must differ"):
        ab.backup_keyring(kr, keys[0], keys[0], tmp_path / "kb", NOW)  # K2 == K1 is refused
    out, ids = ab.backup_keyring(kr, keys[1], keys[0], tmp_path / "kb", NOW)
    assert ids == ["k1", "k2"] and out.name == "abu-keyring-20261125T040000Z.enc"
    assert secret_b64.encode() not in out.read_bytes()
    with pytest.raises(ab.BackupToolError, match="never overwritten"):
        ab.backup_keyring(kr, keys[1], keys[0], tmp_path / "kb", NOW)
    # recovery works with K2 alone, writes a NEW private file and never overwrites
    new = tmp_path / "recovered.json"
    assert ab.restore_keyring(out, keys[1], new) == ["k1", "k2"]
    assert json.loads(new.read_text()) == json.loads(Path(kr).read_text())
    assert stat.S_IMODE(new.stat().st_mode) == 0o600
    with pytest.raises(ab.BackupToolError):
        ab.restore_keyring(out, keys[1], new)  # exists
    # the database key K1 cannot recover the keyring, and a failed recovery leaves no half-written key file
    bad = tmp_path / "bad.json"
    with pytest.raises(ab.BackupToolError):
        ab.restore_keyring(out, keys[0], bad)
    assert not bad.exists()
    assert secret_b64 not in capsys.readouterr().out


def test_a_malformed_keyring_is_refused_before_anything_is_written(tmp_path: Path, keys) -> None:
    bad = tmp_path / "ring.json"
    bad.write_text('{"active": "k1", "keys": {"k1": "AAAA"}}')
    bad.chmod(0o600)
    with pytest.raises(Exception, match="must be 32 bytes|malformed"):
        ab.backup_keyring(str(bad), keys[1], keys[0], tmp_path / "kb", NOW)
    assert not (tmp_path / "kb").exists() or not list((tmp_path / "kb").glob("abu-keyring-*"))


# ------------------------------------------------------------------------------------------------------ CLI
def test_cli_exit_codes_and_no_secret_output(tmp_path: Path, keys, capsys) -> None:
    dest = tmp_path / "b"
    code = ab.main(
        ["backup", "--key-file", keys[0], "--dest", str(dest), "--dump-cmd", *FAILING_DUMP]
    )
    assert code == 1
    assert ab.main(["status", "--dest", str(dest)]) == 2
    out = capsys.readouterr()
    key_b64 = Path(keys[0]).read_text().strip()
    assert key_b64 not in out.out + out.err
    assert ab.main(["status", "--dest", str(tmp_path / "nowhere")]) == 3
