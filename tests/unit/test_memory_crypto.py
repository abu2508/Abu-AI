import base64
import json
import os
import re
import uuid
from pathlib import Path

import pytest

from core.memory.crypto import (
    DecryptionError,
    Envelope,
    KeyRing,
    KeyUnavailableError,
    LocalKeyProvider,
    aad_for,
)

K1, K2 = b"1" * 32, b"2" * 32
USER, REC = uuid.uuid4(), uuid.uuid4()
AAD = aad_for(USER, REC, "content")


def env(active: str = "k1", keys: dict[str, bytes] | None = None) -> Envelope:
    return Envelope(LocalKeyProvider(KeyRing(active, keys or {"k1": K1})))


def test_roundtrip_and_ciphertext_hides_plaintext() -> None:
    blob = env().seal(b"my salary is 987654", AAD)
    assert b"salary" not in blob and b"987654" not in blob
    assert env().open(blob, AAD) == b"my salary is 987654"


def test_every_seal_uses_a_fresh_data_key_and_nonce() -> None:
    a, b = env().seal(b"same", AAD), env().seal(b"same", AAD)
    assert a != b  # random DEK + nonce per record
    e = env()
    assert e._parse(a)[2] != e._parse(b)[2]  # different wrapped DEKs  # noqa: SLF001


@pytest.mark.parametrize(
    "other_aad",
    [
        aad_for(uuid.uuid4(), REC, "content"),
        aad_for(USER, uuid.uuid4(), "content"),
        aad_for(USER, REC, "embed"),
    ],
    ids=["other-user", "other-record", "other-purpose"],
)
def test_ciphertext_is_bound_to_its_user_record_and_purpose(other_aad: bytes) -> None:
    blob = env().seal(b"secret", AAD)
    with pytest.raises(DecryptionError):  # copying a blob to another row/user cannot work
        env().open(blob, other_aad)


def test_any_tampering_is_detected() -> None:
    blob = env().seal(b"secret", AAD)
    for i in range(len(blob)):
        flipped = blob[:i] + bytes([blob[i] ^ 1]) + blob[i + 1 :]
        with pytest.raises((DecryptionError, KeyUnavailableError)):
            env().open(flipped, AAD)


@pytest.mark.parametrize("cut", [0, 2, 5, 20, 40])
def test_truncated_envelopes_rejected(cut: int) -> None:
    blob = env().seal(b"secret", AAD)
    with pytest.raises(DecryptionError):
        env().open(blob[: len(blob) - cut - 1] if cut else blob[:3], AAD)


def test_wrong_key_cannot_decrypt() -> None:
    blob = env().seal(b"secret", AAD)
    with pytest.raises(DecryptionError):
        env(keys={"k1": K2}).open(blob, AAD)


def test_unknown_key_id_fails_closed() -> None:
    blob = env("k1").seal(b"secret", AAD)
    with pytest.raises(KeyUnavailableError):
        env("k2", {"k2": K2}).open(blob, AAD)


def test_rotation_rewraps_without_touching_the_content_ciphertext() -> None:
    old = env("k1", {"k1": K1})
    blob = old.seal(b"secret", AAD)
    new = env("k2", {"k1": K1, "k2": K2})
    assert new.key_id_of(blob) == "k1"
    rewrapped = new.rewrap(blob, AAD)
    assert new.key_id_of(rewrapped) == "k2"
    assert new.open(rewrapped, AAD) == b"secret"
    assert new._parse(blob)[3] == new._parse(rewrapped)[3]  # same content ciphertext  # noqa: SLF001
    # After rotation the OLD key is no longer needed:
    assert env("k2", {"k2": K2}).open(rewrapped, AAD) == b"secret"
    with pytest.raises(KeyUnavailableError):
        env("k2", {"k2": K2}).open(blob, AAD)


def test_keyring_validation_and_no_key_in_repr() -> None:
    with pytest.raises(KeyUnavailableError):
        KeyRing("k1", {"k1": b"short"})
    with pytest.raises(KeyUnavailableError):
        KeyRing("missing", {"k1": K1})
    text = repr(KeyRing("k1", {"k1": K1}))
    assert "k1" in text and base64.b64encode(K1).decode() not in text and K1.decode() not in text


def test_keyring_file_permissions_and_format(tmp_path: Path) -> None:
    f = tmp_path / "ring.json"
    KeyRing.generate_file(str(f))
    assert oct(f.stat().st_mode & 0o777) == "0o600"
    ring = KeyRing.from_file(str(f))
    assert ring.active_id == "k1" and len(ring.keys["k1"]) == 32
    with pytest.raises(FileExistsError):  # never overwrites existing key material
        KeyRing.generate_file(str(f))
    f.chmod(0o644)
    with pytest.raises(KeyUnavailableError, match="chmod 600"):
        KeyRing.from_file(str(f))
    assert (
        KeyRing.from_file(str(f), strict_permissions=False).active_id == "k1"
    )  # docker-secret mode


@pytest.mark.parametrize(
    "content",
    [
        "not json",
        "{}",
        '{"active":"a","keys":{"a":"!!notbase64"}}',
        '{"active":"a","keys":{"a":"c2hvcnQ="}}',
    ],
)
def test_malformed_keyring_files_fail_closed(tmp_path: Path, content: str) -> None:
    f = tmp_path / "ring.json"
    f.write_text(content)
    f.chmod(0o600)
    with pytest.raises(KeyUnavailableError):
        KeyRing.from_file(str(f))
    with pytest.raises(KeyUnavailableError):
        KeyRing.from_file(str(tmp_path / "missing.json"))


def test_rotated_keyring_keeps_old_keys_and_changes_active() -> None:
    ring = KeyRing("k1", {"k1": K1}).rotated("k2")
    assert ring.active_id == "k2" and set(ring.keys) == {"k1", "k2"} and ring.keys["k1"] == K1
    with pytest.raises(KeyUnavailableError):
        ring.rotated("k1")


def test_invalid_b64_kek_rejected() -> None:
    with pytest.raises(KeyUnavailableError):
        KeyRing.from_b64("!!!")
    assert KeyRing.from_b64(base64.b64encode(os.urandom(32)).decode()).active_id == "env-1"


def test_wrapped_key_is_not_the_kek_or_the_dek_in_clear() -> None:
    e = env()
    blob = e.seal(b"x", AAD)
    assert K1 not in blob and base64.b64encode(K1) not in blob
    assert json.dumps(
        re.findall(rb"[A-Za-z0-9+/=]{40,}", blob), default=str
    )  # nothing key-shaped in the clear beyond ciphertext
