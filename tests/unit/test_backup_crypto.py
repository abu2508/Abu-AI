import importlib.util
import io
import os
import struct
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "infrastructure/scripts/encrypted_backup.py"
spec = importlib.util.spec_from_file_location("encrypted_backup", SCRIPT)
assert spec and spec.loader
bk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bk)

KEY = os.urandom(32)


def enc(data: bytes, key: bytes = KEY) -> bytes:
    out = io.BytesIO()
    bk.encrypt_stream(io.BytesIO(data), out, key)
    return out.getvalue()


def dec(blob: bytes, key: bytes = KEY) -> bytes:
    out = io.BytesIO()
    bk.decrypt_stream(io.BytesIO(blob), out, key)
    return out.getvalue()


@pytest.mark.parametrize(
    "size", [0, 1, 100, bk.CHUNK - 1, bk.CHUNK, bk.CHUNK + 1, 3 * bk.CHUNK, 3 * bk.CHUNK + 17]
)
def test_roundtrip_all_boundary_sizes(size: int) -> None:
    data = os.urandom(size)
    blob = enc(data)
    assert dec(blob) == data
    if size > 32:
        assert data[:32] not in blob  # nothing readable in the backup


def test_two_backups_of_the_same_data_differ() -> None:
    assert enc(b"x" * 1000) != enc(b"x" * 1000)  # fresh salt


def test_wrong_key_rejected() -> None:
    with pytest.raises(bk.BackupError, match="authentication"):
        dec(enc(b"secret" * 100), os.urandom(32))


def test_every_single_byte_flip_is_detected() -> None:
    blob = enc(b"secret-database-dump" * 20)
    for i in range(len(blob)):
        bad = blob[:i] + bytes([blob[i] ^ 1]) + blob[i + 1 :]
        with pytest.raises(bk.BackupError):
            dec(bad)


def _chunks(blob: bytes) -> tuple[bytes, list[bytes]]:
    head, rest, parts = blob[:11], blob[11:], []
    while rest:
        (n,) = struct.unpack(">I", rest[:4])
        parts.append(rest[: 4 + n])
        rest = rest[4 + n :]
    return head, parts


def test_truncation_dropping_the_last_chunk_is_detected() -> None:
    head, parts = _chunks(enc(os.urandom(3 * bk.CHUNK + 5)))
    assert len(parts) == 4
    with pytest.raises(bk.BackupError):
        dec(
            head + b"".join(parts[:-1])
        )  # a clean cut at a chunk boundary still fails (no FINAL chunk)
    with pytest.raises(bk.BackupError):
        dec(head + b"".join(parts)[:-3])  # mid-chunk cut


def test_chunk_reordering_and_duplication_detected() -> None:
    head, parts = _chunks(enc(os.urandom(3 * bk.CHUNK + 5)))
    with pytest.raises(bk.BackupError):
        dec(head + parts[1] + parts[0] + parts[2] + parts[3])
    with pytest.raises(bk.BackupError):
        dec(head + parts[0] + parts[0] + parts[1] + parts[2] + parts[3])


def test_trailing_data_and_foreign_files_rejected() -> None:
    with pytest.raises(bk.BackupError, match="trailing"):
        dec(enc(b"abc") + b"extra")
    with pytest.raises(bk.BackupError, match="not an Abu backup"):
        dec(b"PGDMP-not-ours")
    with pytest.raises(bk.BackupError):
        dec(b"")


def test_key_file_rules(tmp_path: Path) -> None:
    k = tmp_path / "k"
    bk.genkey(str(k))
    assert oct(k.stat().st_mode & 0o777) == "0o600"
    assert len(bk.load_key(str(k))) == 32
    with pytest.raises(FileExistsError):
        bk.genkey(str(k))
    k.chmod(0o640)
    with pytest.raises(bk.BackupError, match="chmod 600"):
        bk.load_key(str(k))
    (tmp_path / "bad").write_text("not base64!!")
    (tmp_path / "bad").chmod(0o600)
    with pytest.raises(bk.BackupError):
        bk.load_key(str(tmp_path / "bad"))


def test_command_line_pipeline(tmp_path: Path) -> None:
    key = tmp_path / "k"

    def run(*a: str, input: bytes | None = None) -> subprocess.CompletedProcess[bytes]:  # noqa: A002
        return subprocess.run(  # noqa: S603
            [sys.executable, str(SCRIPT), *a], capture_output=True, check=False, input=input
        )

    assert run("genkey", "--key-file", str(key)).returncode == 0
    data = os.urandom(200_000)
    encrypted = run("encrypt", "--key-file", str(key), input=data)
    assert encrypted.returncode == 0 and data[:64] not in encrypted.stdout
    assert run("decrypt", "--key-file", str(key), input=encrypted.stdout).stdout == data
    tampered = bytearray(encrypted.stdout)
    tampered[50] ^= 0xFF
    bad = run("decrypt", "--key-file", str(key), input=bytes(tampered))
    assert bad.returncode == 1 and b"authentication failed" in bad.stderr and bad.stdout == b""
