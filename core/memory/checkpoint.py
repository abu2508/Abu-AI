"""LangGraph PostgreSQL checkpointer (working memory), encrypted and isolated.

Findings that shape this module (verified against langgraph-checkpoint-postgres 3.2.1):
  1. The saver stores PRIMITIVE channel values (str/int/float/bool/None) INLINE, as plaintext JSON, in
     checkpoints.checkpoint - they bypass the serializer, so a serializer-level cipher alone would leave
     string state unencrypted. EncryptedPostgresSaver therefore boxes every primitive into a dict so the
     saver writes it to checkpoint_blobs, where the serializer encrypts it. (Test: raw-table scan.)
  2. EncryptedSerializer happily loads UNENCRYPTED data. StrictEncryptedSerializer refuses it, so a
     plaintext row planted by someone with database write access is never deserialised.
  3. The cipher hook receives no row context, so checkpoint ciphertext uses a fixed AAD label: it is
     confidential and authenticated, but (unlike memory records) not bound to a specific row.
  4. checkpoints.metadata stays plaintext JSON (source/step/parents). Never put content in config metadata.

Tables live in the separate schema `lg_checkpoints` (search_path), apart from long-term memory, and
Alembic ignores them. Ownership/retention/deletion go through agent_threads (core/memory/threads.py).
The class overrides ONE protected method; the dependency is pinned exactly and regression-tested.
"""

from typing import Any, cast

from langgraph.checkpoint.base import CheckpointTuple
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from langgraph.checkpoint.serde.base import CipherProtocol
from langgraph.checkpoint.serde.encrypted import EncryptedSerializer
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from core.memory.crypto import DecryptionError, Envelope

SCHEMA = "lg_checkpoints"
BOX_KEY = "__abu_boxed__"
_AAD = b"abu/checkpoint/v1"
_NAME = "abu1"


class EnvelopeCipher(CipherProtocol):
    def __init__(self, envelope: Envelope) -> None:
        self._env = envelope

    def encrypt(self, plaintext: bytes) -> tuple[str, bytes]:
        return _NAME, self._env.seal(plaintext, _AAD)

    def decrypt(self, ciphername: str, ciphertext: bytes) -> bytes:
        if ciphername != _NAME:
            raise DecryptionError("unknown checkpoint cipher")
        return self._env.open(ciphertext, _AAD)


class StrictEncryptedSerializer(EncryptedSerializer):
    def loads_typed(self, data: tuple[str, bytes]) -> Any:
        if "+" not in data[0]:
            raise DecryptionError("refusing to load unencrypted checkpoint data")
        return super().loads_typed(data)


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, str | int | float | bool)


class EncryptedPostgresSaver(AsyncPostgresSaver):
    async def aput(self, config: Any, checkpoint: Any, metadata: Any, new_versions: Any) -> Any:
        boxed: dict[str, Any] = {}
        for channel, value in checkpoint["channel_values"].items():
            if isinstance(value, dict) and BOX_KEY in value:
                raise ValueError(f"channel value uses the reserved key {BOX_KEY!r}")
            # Primitives would be written inline as plaintext JSON. Box them so they go to blobs.
            boxed[channel] = {BOX_KEY: value} if _is_primitive(value) else value
        sealed = checkpoint.copy()
        sealed["channel_values"] = boxed
        return await super().aput(config, sealed, metadata, new_versions)

    async def _load_checkpoint_tuple(self, value: Any) -> CheckpointTuple:
        tup = await super()._load_checkpoint_tuple(value)
        values = {
            k: (v[BOX_KEY] if isinstance(v, dict) and set(v) == {BOX_KEY} else v)
            for k, v in tup.checkpoint["channel_values"].items()
        }
        return tup._replace(checkpoint={**tup.checkpoint, "channel_values": values})


def checkpoint_dsn(database_url: str) -> str:
    """SQLAlchemy URL (postgresql+psycopg://) -> libpq DSN."""
    return database_url.replace("postgresql+psycopg://", "postgresql://", 1)


async def open_checkpointer(
    database_url: str,
    envelope: Envelope,
    *,
    min_size: int = 1,
    max_size: int = 4,
    setup: bool = True,
) -> tuple[AsyncConnectionPool, EncryptedPostgresSaver]:
    """`setup=False` is for a least-privilege runtime role: the tables already exist (the owner-run bootstrap step
    created them with `provision_checkpoint_tables`) and this role has no DDL rights."""
    pool = AsyncConnectionPool(
        checkpoint_dsn(database_url),
        min_size=min_size,
        max_size=max_size,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
            "row_factory": dict_row,
            "options": f"-c search_path={SCHEMA}",  # nothing is ever created in `public`
        },
        open=False,
    )
    await pool.open(wait=True)
    saver = EncryptedPostgresSaver(
        cast(Any, pool), serde=StrictEncryptedSerializer(EnvelopeCipher(envelope))
    )
    if setup:
        await saver.setup()  # idempotent: LangGraph's own migrations, inside lg_checkpoints
    return pool, saver


def provision_checkpoint_tables(owner_database_url: str) -> None:
    """Create/upgrade LangGraph's checkpoint tables as the OWNER role (bootstrap step only; never the runtime role).
    Uses the library's own migrations, so the result is identical to what `saver.setup()` would create."""
    import psycopg
    from langgraph.checkpoint.postgres import PostgresSaver

    with psycopg.connect(
        checkpoint_dsn(owner_database_url), autocommit=True, row_factory=dict_row
    ) as conn:
        conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')
        conn.execute(f'SET search_path TO "{SCHEMA}"')
        PostgresSaver(cast(Any, conn)).setup()
