# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,union-attr,index,attr-defined,assignment,operator,misc,type-arg"
"""Gate G3 tripwire (P1 review). `lg_checkpoints.checkpoints.metadata` is PLAINTEXT JSON (docs/operations/G3_CHECKPOINT_METADATA.md).
This test does not decide the gate; it makes the current, measured exposure a hard invariant: only structural keys, never
content, and nothing in the code base may pass LangGraph config metadata."""

import base64
import os
import re
from pathlib import Path

import psycopg
from sqlalchemy.engine import make_url

from core.memory.checkpoint import open_checkpointer
from core.memory.crypto import Envelope, KeyRing, LocalKeyProvider
from tests.integration.test_memory_checkpoints import CP_MARK, run

ROOT = Path(__file__).resolve().parents[2]
ALLOWED_KEYS = {"step", "source", "parents"}
ALLOWED_SOURCES = {"input", "loop", "update", "fork"}


async def test_plaintext_checkpoint_metadata_holds_only_structural_keys(migrated_url) -> None:
    env = Envelope(LocalKeyProvider(KeyRing.from_b64(base64.b64encode(os.urandom(32)).decode())))
    pool, saver = await open_checkpointer(migrated_url.render_as_string(hide_password=False), env)
    try:
        await run(
            saver, "g3-tripwire-thread"
        )  # the graph writes the marker CP_MARK into every state value
    finally:
        await pool.close()
    dsn = (
        make_url(migrated_url)
        .render_as_string(hide_password=False)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )
    with psycopg.connect(dsn) as c:
        rows = c.execute(
            "SELECT metadata, thread_id, checkpoint_ns FROM lg_checkpoints.checkpoints"
        ).fetchall()
    assert rows, "the graph run produced no checkpoints"
    for metadata, thread_id, ns in rows:
        assert set(metadata) <= ALLOWED_KEYS, (
            f"unexpected plaintext metadata keys: {sorted(set(metadata) - ALLOWED_KEYS)}"
        )
        assert metadata["source"] in ALLOWED_SOURCES and isinstance(metadata["step"], int)
        assert (
            CP_MARK not in str(metadata) and CP_MARK not in f"{thread_id}{ns}"
        )  # content never reaches the plaintext columns
        assert re.fullmatch(r"[A-Za-z0-9_-]{1,64}", thread_id)  # an opaque id, not text


def test_no_code_passes_langgraph_config_metadata() -> None:
    """Anything in `config["metadata"]` is copied into the plaintext metadata column. Nothing may set it."""
    offenders = []
    for base in ("core", "agents", "domain", "services"):
        for path in (ROOT / base).rglob("*.py"):
            text = path.read_text()
            if re.search(
                r"""\{\s*["']configurable["'][^}]*\}[^\n]*["']metadata["']|["']metadata["']\s*:\s*\{[^}]*thread""",
                text,
            ):
                offenders.append(str(path.relative_to(ROOT)))
            if re.search(
                r"""ainvoke\([^)]*metadata\s*=|astream\([^)]*metadata\s*=|with_config\([^)]*metadata\s*=""",
                text,
                re.S,
            ):
                offenders.append(str(path.relative_to(ROOT)))
    assert not offenders, f"config metadata is passed to LangGraph in: {sorted(set(offenders))}"
