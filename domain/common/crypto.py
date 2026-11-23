"""Per-field encryption and keyed hashing for domain data. Reuses the memory keyring/envelope: one key
system, keys outside the database. AAD binds each ciphertext to (user, row, purpose) so a value copied
to another row or user cannot be decrypted."""

import hashlib
import hmac
import uuid

from pydantic import SecretStr

from core.memory.crypto import Envelope, aad_for


class FieldCipher:
    def __init__(self, envelope: Envelope, hash_key: SecretStr) -> None:
        self._env = envelope
        self._key = hash_key.get_secret_value().encode()

    def seal(self, user_id: uuid.UUID, row_id: uuid.UUID, purpose: str, text: str) -> bytes:
        return self._env.seal(text.encode(), aad_for(user_id, row_id, purpose))

    def open(self, user_id: uuid.UUID, row_id: uuid.UUID, purpose: str, blob: bytes) -> str:
        return self._env.open(blob, aad_for(user_id, row_id, purpose)).decode()

    def hash(self, user_id: uuid.UUID, namespace: str, *parts: str) -> str:
        """Keyed, per-user hash: equal inputs match within one user, never across users or without the key."""
        msg = "\x1f".join([str(user_id), namespace, *parts]).encode()
        return hmac.new(self._key, msg, hashlib.sha256).hexdigest()
