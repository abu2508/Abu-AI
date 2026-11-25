"""Provider-independent embedding interface.

ONLY a deterministic local TEST embedder exists. It is NOT a semantic model: it hashes words and
word pairs into a fixed-size vector, so it measures lexical overlap, not meaning. It exists so the
whole memory pipeline (approval gating, vector search, deletion, isolation) can be built and tested
without any external service and without any personal data leaving the process.

Production embedding providers are configurable but DISABLED: selecting any non-local embedder is
refused until the operator sets embedding_external_approved after reviewing the provider's data
policy, retention, training practices and pricing. Nothing is registered for external providers, on
purpose (no placeholder integrations).

Changing provider or dimension is explicit: the dimension is fixed by the migration that created
memory_embeddings.embedding; a different dimension needs a new migration plus an explicit re-embed
(`admin memory reembed`). The application refuses to embed on a mismatch.
"""

import hashlib
import math
import re
import unicodedata
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from database.schemas import EMBEDDING_DIM, DataClass


class EmbeddingConfigError(Exception):
    """Embedding configuration is not acceptable. The application will not embed anything."""


class Embedder(Protocol):
    id: str
    dim: int
    is_semantic: bool  # True only for real semantic models
    is_local: bool  # True = text never leaves this process
    max_data_class: DataClass  # the most sensitive data this embedder may receive

    async def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


_WORD = re.compile(r"[a-z0-9]+")


class LocalTestHashEmbedder:
    """Deterministic feature-hashing embedder. TEST/DEVELOPMENT ONLY - lexical, not semantic."""

    id = "local-test-hash-v1"
    dim = EMBEDDING_DIM
    is_semantic = False
    is_local = True
    max_data_class = DataClass.SENSITIVE  # nothing leaves the process

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._one(t) for t in texts]

    def _one(self, text: str) -> list[float]:
        words = _WORD.findall(unicodedata.normalize("NFKC", text).lower())
        if not words:
            raise ValueError("cannot embed empty text")
        features = words + [f"{a} {b}" for a, b in zip(words, words[1:], strict=False)]
        vec = [0.0] * self.dim
        for feature in features:
            digest = hashlib.sha256(feature.encode()).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vec[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vec))
        if norm == 0:  # features cancelled out exactly: fall back to a fixed unit vector
            vec[0], norm = 1.0, 1.0
        return [v / norm for v in vec]


@dataclass(frozen=True)
class EmbedderSpec:
    factory: Callable[[], Embedder]
    is_local: bool


# Registered embedders. To add a real provider: implement Embedder, register it here, set
# is_local=False, then have it approved (embedding_external_approved) in configuration.
REGISTRY: dict[str, EmbedderSpec] = {
    "local-test-hash": EmbedderSpec(LocalTestHashEmbedder, is_local=True),
}


def build_embedder(
    name: str, *, external_approved: bool, env: str, allow_test_in_production: bool
) -> Embedder:
    spec = REGISTRY.get(name)
    if spec is None:
        raise EmbeddingConfigError(f"unknown embedder {name!r}; registered: {sorted(REGISTRY)}")
    if not spec.is_local and not external_approved:
        raise EmbeddingConfigError(
            f"embedder {name!r} is an external provider and is disabled until its data policy, "
            "retention, training practices and pricing are approved"
        )
    embedder = spec.factory()
    if (
        not embedder.is_semantic
        and env in ("production", "staging")
        and not allow_test_in_production
    ):
        raise EmbeddingConfigError(
            f"embedder {name!r} is a TEST embedder (lexical, not semantic) and is not allowed in production "
            "without ABU_ALLOW_TEST_EMBEDDER_IN_PRODUCTION=true"
        )
    if embedder.dim != EMBEDDING_DIM:
        raise EmbeddingConfigError(
            f"embedder {embedder.id!r} has dimension {embedder.dim} but the database column is {EMBEDDING_DIM}: "
            "add a migration for the new dimension and run an explicit re-embed"
        )
    return embedder


def sensitivity_to_data_class(sensitivity: str) -> DataClass:
    """What an embedder would be RECEIVING. Anything about a person is SENSITIVE for this purpose."""
    return DataClass.NON_SENSITIVE if sensitivity == "low" else DataClass.SENSITIVE
