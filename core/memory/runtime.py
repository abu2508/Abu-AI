"""Assembles the memory runtime (keys, embedder, checkpointer) from Settings.

Fail-closed: no key material => no memory (KeyUnavailableError); an unapproved/unknown embedder or a
dimension mismatch => EmbeddingConfigError. Used by the API lifespan and the admin CLI.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from psycopg_pool import AsyncConnectionPool
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from core.governance.service import Governance
from core.memory.checkpoint import EncryptedPostgresSaver, open_checkpointer
from core.memory.crypto import Envelope, KeyProvider, KeyRing, KeyUnavailableError, LocalKeyProvider
from core.memory.embeddings import Embedder, EmbeddingConfigError, build_embedder
from core.memory.service import MemoryPolicy, MemoryService
from core.memory.structured import StructuredRegistry, default_registry
from core.memory.threads import ThreadService
from database.schemas import EMBEDDING_DIM
from services.api.config import Settings


def build_keyring(settings: Settings) -> KeyRing:
    if settings.memory_keyring_file:
        return KeyRing.from_file(
            settings.memory_keyring_file,
            strict_permissions=settings.memory_keyring_strict_permissions,
        )
    if settings.memory_kek is not None:
        if settings.env in ("production", "staging"):
            raise KeyUnavailableError(
                "production and staging require a keyring FILE (ABU_MEMORY_KEYRING_FILE), not an env key"
            )
        return KeyRing.from_b64(settings.memory_kek.get_secret_value())
    raise KeyUnavailableError(
        "no memory key material: set ABU_MEMORY_KEYRING_FILE (or ABU_MEMORY_KEK in development)"
    )


def build_provider(settings: Settings) -> KeyProvider:
    return LocalKeyProvider(build_keyring(settings))


def build_embedder_from_settings(settings: Settings) -> Embedder:
    return build_embedder(
        settings.embedder,
        external_approved=settings.embedding_external_approved,
        env=settings.env,
        allow_test_in_production=settings.allow_test_embedder_in_production,
    )


async def verify_embedding_dimension(session: AsyncSession, embedder: Embedder) -> None:
    """The vector column's dimension is fixed by migration; refuse to run on a mismatch."""
    dim = (
        await session.execute(
            text(
                "SELECT a.atttypmod FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
                "WHERE c.relname = 'memory_embeddings' AND a.attname = 'embedding' AND NOT a.attisdropped"
            )
        )
    ).scalar_one_or_none()
    if dim is None:
        raise EmbeddingConfigError("memory_embeddings table is missing: run migrations")
    if dim != embedder.dim or dim != EMBEDDING_DIM:
        raise EmbeddingConfigError(
            f"embedder dimension {embedder.dim} != database vector dimension {dim}: add a migration and re-embed explicitly"
        )


@dataclass
class MemoryRuntime:
    envelope: Envelope
    embedder: Embedder
    policy: MemoryPolicy
    structured: StructuredRegistry
    pool: AsyncConnectionPool | None = None
    saver: EncryptedPostgresSaver | None = None

    def service(
        self,
        session: AsyncSession,
        governance: Governance,
        *,
        clock: Callable[[], datetime] | None = None,
        thread_days: int = 7,
    ) -> MemoryService:
        threads = (
            ThreadService(session, self.saver, retention_days=thread_days, clock=clock)
            if self.saver
            else None
        )
        return MemoryService(
            session,
            self.envelope,
            self.embedder,
            governance,
            self.policy,
            threads=threads,
            structured=self.structured,
            clock=clock,
        )

    def threads(
        self,
        session: AsyncSession,
        *,
        thread_days: int = 7,
        clock: Callable[[], datetime] | None = None,
    ) -> ThreadService | None:
        return (
            ThreadService(session, self.saver, retention_days=thread_days, clock=clock)
            if self.saver
            else None
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()


async def open_runtime(settings: Settings, *, with_checkpointer: bool = True) -> MemoryRuntime:
    envelope = Envelope(build_provider(settings))
    embedder = build_embedder_from_settings(settings)
    rt = MemoryRuntime(envelope, embedder, MemoryPolicy.from_settings(settings), default_registry())
    if with_checkpointer and settings.database_url is not None:
        rt.pool, rt.saver = await open_checkpointer(
            settings.database_url.get_secret_value(),
            envelope,
            setup=settings.checkpoint_auto_setup,
        )
    return rt
