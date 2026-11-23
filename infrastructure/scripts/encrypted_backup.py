#!/usr/bin/env python3
"""Encrypted database backups: authenticated, chunked AES-256-GCM, stdin -> stdout.

    python3 encrypted_backup.py genkey  --key-file ~/.abu-backup.key
    docker compose ... exec -T db pg_dump -Fc -U ... DB | python3 encrypted_backup.py encrypt --key-file K > backup.enc
    python3 encrypted_backup.py decrypt --key-file K < backup.enc | docker compose ... exec -T db pg_restore ...

Properties (each is tested):
  * confidentiality + integrity: AES-256-GCM per 64 KiB chunk, any bit flip is detected;
  * no reordering / truncation: the chunk index and a FINAL flag are in the AAD, so dropping or
    swapping chunks, or cutting the stream short, fails decryption;
  * the key is a file OUTSIDE the database and outside the backup (mode 600, refuses to overwrite);
  * streaming: constant memory, works on large dumps.
This protects BACKUPS. Protecting the live data volume needs host disk encryption (README).
"""

import argparse
import base64
import os
import stat
import struct
import sys
from typing import BinaryIO

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

MAGIC = b"ABUBK1\n"
CHUNK = 64 * 1024
TAG = 16


class BackupError(Exception):
    pass


def _aad(index: int, final: bool) -> bytes:
    return MAGIC + struct.pack(">QB", index, 1 if final else 0)


def load_key(path: str, strict: bool = True) -> bytes:
    try:
        mode = os.stat(path).st_mode
        with open(path) as fh:
            key = base64.b64decode(fh.read().strip(), validate=True)
    except (OSError, ValueError) as exc:
        raise BackupError("key file unreadable or not base64") from exc
    if strict and mode & (stat.S_IRWXG | stat.S_IRWXO):
        raise BackupError("key file must not be accessible by group/others (chmod 600)")
    if len(key) != 32:
        raise BackupError("key must be 32 bytes")
    return key


def genkey(path: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(base64.b64encode(os.urandom(32)).decode())


def _read_full(src: BinaryIO, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        part = src.read(n - len(buf))
        if not part:
            break
        buf += part
    return buf


def encrypt_stream(src: BinaryIO, dst: BinaryIO, key: bytes) -> None:
    aes = AESGCM(key)
    salt = os.urandom(4)  # nonce = salt(4) | counter(8): unique per chunk, per backup
    dst.write(MAGIC + salt)
    index = 0
    cur = _read_full(src, CHUNK)
    while True:
        nxt = _read_full(src, CHUNK)
        final = not nxt
        nonce = salt + struct.pack(">Q", index)
        ct = aes.encrypt(nonce, cur, _aad(index, final))
        dst.write(struct.pack(">I", len(ct)) + ct)
        if final:
            return
        cur, index = nxt, index + 1


def decrypt_stream(src: BinaryIO, dst: BinaryIO, key: bytes) -> None:
    aes = AESGCM(key)
    head = _read_full(src, len(MAGIC) + 4)
    if head[: len(MAGIC)] != MAGIC or len(head) != len(MAGIC) + 4:
        raise BackupError("not an Abu backup")
    salt = head[len(MAGIC) :]
    index = 0
    while True:
        raw = _read_full(src, 4)
        if len(raw) != 4:
            raise BackupError("backup truncated (no final chunk)")
        (length,) = struct.unpack(">I", raw)
        if length < TAG or length > CHUNK + TAG:
            raise BackupError("backup corrupted (bad chunk length)")
        ct = _read_full(src, length)
        if len(ct) != length:
            raise BackupError("backup truncated")
        nonce = salt + struct.pack(">Q", index)
        pt = None
        for final in (False, True):  # the writer marks only the LAST chunk final
            try:
                pt = aes.decrypt(nonce, ct, _aad(index, final))
                is_final = final
                break
            except InvalidTag:
                continue
        if pt is None:
            raise BackupError("authentication failed: wrong key, corruption or tampering")
        dst.write(pt)
        if is_final:
            if src.read(1):
                raise BackupError("trailing data after the final chunk")
            return
        index += 1


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("cmd", choices=["genkey", "encrypt", "decrypt"])
    p.add_argument("--key-file", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "genkey":
            genkey(a.key_file)
            print(
                f"created {a.key_file} (mode 600). Keep it OFF the database host's backup target.",
                file=sys.stderr,
            )
            return 0
        key = load_key(a.key_file)
        (encrypt_stream if a.cmd == "encrypt" else decrypt_stream)(
            sys.stdin.buffer, sys.stdout.buffer, key
        )
    except BackupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
