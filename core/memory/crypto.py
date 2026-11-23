"""Envelope encryption for stored memory content.

Design
  * Every record gets its OWN random 256-bit data key (DEK). Content is sealed with AES-256-GCM.
  * The DEK is wrapped (AES-256-GCM) by a key-encryption key (KEK) that lives OUTSIDE PostgreSQL, in a
    keyring file or the environment. The database holds only: ciphertext, nonce, wrapped DEK, KEK id.
    Keys are never stored beside the data they protect.
  * Both layers are bound to a context (AAD) such as "user|record|purpose": a ciphertext copied to
    another row or another user's record fails to decrypt (and fails loudly).
  * Rotation re-wraps DEKs under the active KEK without touching the content ciphertext.

KeyProvider is the abstraction: LocalKeyProvider reads a keyring today; a cloud KMS (or an HSM) can
implement the same two methods later without changing any caller.

What this does NOT do: it cannot protect against an attacker who controls the running application
process or can read the keyring. Disk and backup encryption (README) cover the "stolen volume" case.
"""

import base64
import json
import os
import stat
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"AE1"  # envelope format version 1
KEY_BYTES = 32
NONCE_BYTES = 12


class KeyUnavailableError(Exception):
    """No usable key material. Memory operations fail closed."""


class DecryptionError(Exception):
    """Wrong key, wrong context, truncated or tampered data. Never includes plaintext."""


@dataclass(frozen=True)
class KeyRing:
    active_id: str
    keys: Mapping[str, bytes]

    def __post_init__(self) -> None:
        if self.active_id not in self.keys:
            raise KeyUnavailableError("active key id is not in the keyring")
        for key_id, key in self.keys.items():
            if len(key) != KEY_BYTES or not (1 <= len(key_id.encode()) <= 64):
                raise KeyUnavailableError(
                    f"key {key_id!r} must be {KEY_BYTES} bytes with a short id"
                )

    def __repr__(self) -> str:  # never print key material
        return f"KeyRing(active_id={self.active_id!r}, key_ids={sorted(self.keys)})"

    @classmethod
    def from_file(cls, path: str, *, strict_permissions: bool = True) -> "KeyRing":
        p = Path(path)
        try:
            mode = p.stat().st_mode
            raw = json.loads(p.read_text())
        except (OSError, ValueError) as exc:
            raise KeyUnavailableError("keyring file unreadable") from exc
        if strict_permissions and mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise KeyUnavailableError(
                "keyring file must not be accessible by group/others (chmod 600)"
            )
        try:
            keys = {k: base64.b64decode(v, validate=True) for k, v in raw["keys"].items()}
            return cls(raw["active"], keys)
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise KeyUnavailableError("keyring file malformed") from exc

    @classmethod
    def from_b64(cls, b64: str, key_id: str = "env-1") -> "KeyRing":
        try:
            key = base64.b64decode(b64, validate=True)
        except ValueError as exc:
            raise KeyUnavailableError("memory KEK is not valid base64") from exc
        return cls(key_id, {key_id: key})

    @staticmethod
    def generate_file(path: str, key_id: str = "k1") -> None:
        """Create a new keyring file (mode 600). Refuses to overwrite."""
        data = {
            "active": key_id,
            "keys": {key_id: base64.b64encode(os.urandom(KEY_BYTES)).decode()},
        }
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump(data, fh)

    def rotated(self, new_id: str) -> "KeyRing":
        """A keyring with a fresh active key; old keys stay for decrypting/re-wrapping."""
        if new_id in self.keys:
            raise KeyUnavailableError("key id already exists")
        return KeyRing(new_id, {**self.keys, new_id: os.urandom(KEY_BYTES)})


class KeyProvider(Protocol):
    @property
    def active_key_id(self) -> str: ...

    def wrap(self, dek: bytes, aad: bytes) -> tuple[str, bytes]: ...

    def unwrap(self, key_id: str, wrapped: bytes, aad: bytes) -> bytes: ...


def _wrap_key(kek: bytes) -> bytes:
    # Domain-separated subkey so the raw KEK is never used directly for AEAD.
    return HKDF(
        algorithm=hashes.SHA256(), length=KEY_BYTES, salt=None, info=b"abu/memory/kek-wrap/v1"
    ).derive(kek)


class LocalKeyProvider:
    def __init__(self, ring: KeyRing) -> None:
        self._ring = ring

    @property
    def active_key_id(self) -> str:
        return self._ring.active_id

    def wrap(self, dek: bytes, aad: bytes) -> tuple[str, bytes]:
        nonce = os.urandom(NONCE_BYTES)
        key_id = self._ring.active_id
        return key_id, nonce + AESGCM(_wrap_key(self._ring.keys[key_id])).encrypt(nonce, dek, aad)

    def unwrap(self, key_id: str, wrapped: bytes, aad: bytes) -> bytes:
        kek = self._ring.keys.get(key_id)
        if kek is None:
            raise KeyUnavailableError(f"key {key_id!r} is not in the keyring")
        if len(wrapped) < NONCE_BYTES + 16:
            raise DecryptionError("wrapped key truncated")
        try:
            return AESGCM(_wrap_key(kek)).decrypt(wrapped[:NONCE_BYTES], wrapped[NONCE_BYTES:], aad)
        except InvalidTag:
            raise DecryptionError("wrapped key failed authentication") from None


def aad_for(user_id: object, record_id: object, purpose: str) -> bytes:
    """Context binding. Changing user, record or purpose makes the ciphertext undecryptable."""
    return f"abu/mem/v1|{user_id}|{record_id}|{purpose}".encode()


class Envelope:
    """seal()/open() byte blobs. Layout: MAGIC | u8 len(kek_id) | kek_id | nonce | u16 len(wrapped) | wrapped | ct."""

    def __init__(self, provider: KeyProvider) -> None:
        self._p = provider

    def seal(self, plaintext: bytes, aad: bytes) -> bytes:
        dek, nonce = os.urandom(KEY_BYTES), os.urandom(NONCE_BYTES)
        ciphertext = AESGCM(dek).encrypt(nonce, plaintext, aad)
        key_id, wrapped = self._p.wrap(dek, aad)
        kid = key_id.encode()
        return (
            MAGIC
            + struct.pack(">B", len(kid))
            + kid
            + nonce
            + struct.pack(">H", len(wrapped))
            + wrapped
            + ciphertext
        )

    @staticmethod
    def _parse(blob: bytes) -> tuple[str, bytes, bytes, bytes]:
        try:
            if blob[:3] != MAGIC:
                raise DecryptionError("not an envelope")
            klen = blob[3]
            i = 4
            key_id = blob[i : i + klen].decode()
            i += klen
            nonce = blob[i : i + NONCE_BYTES]
            i += NONCE_BYTES
            (wlen,) = struct.unpack(">H", blob[i : i + 2])
            i += 2
            wrapped = blob[i : i + wlen]
            ciphertext = blob[i + wlen :]
            if len(nonce) != NONCE_BYTES or len(wrapped) != wlen or len(ciphertext) < 16:
                raise DecryptionError("envelope truncated")
            return key_id, nonce, wrapped, ciphertext
        except (IndexError, struct.error, UnicodeDecodeError):
            raise DecryptionError("envelope malformed") from None

    def open(self, blob: bytes, aad: bytes) -> bytes:
        key_id, nonce, wrapped, ciphertext = self._parse(blob)
        dek = self._p.unwrap(key_id, wrapped, aad)
        try:
            return AESGCM(dek).decrypt(nonce, ciphertext, aad)
        except InvalidTag:
            raise DecryptionError(
                "content failed authentication (wrong key, context or tampering)"
            ) from None

    def key_id_of(self, blob: bytes) -> str:
        return self._parse(blob)[0]

    def rewrap(self, blob: bytes, aad: bytes) -> bytes:
        """Re-wrap the DEK under the ACTIVE key. The content ciphertext is not touched."""
        key_id, nonce, wrapped, ciphertext = self._parse(blob)
        dek = self._p.unwrap(key_id, wrapped, aad)
        new_id, new_wrapped = self._p.wrap(dek, aad)
        kid = new_id.encode()
        return (
            MAGIC
            + struct.pack(">B", len(kid))
            + kid
            + nonce
            + struct.pack(">H", len(new_wrapped))
            + new_wrapped
            + ciphertext
        )
