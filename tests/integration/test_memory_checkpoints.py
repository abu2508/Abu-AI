"""LangGraph working memory: encrypted checkpoints, thread ownership, retention, and deletion that
reaches every historical checkpoint. Uses the REAL encrypted PostgreSQL saver."""

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, TypedDict

import pytest
from langgraph.graph import END, StateGraph
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession

from core.governance.service import Governance
from core.memory.agent import AgentMemory
from core.memory.checkpoint import (
    BOX_KEY,
    EncryptedPostgresSaver,
    StrictEncryptedSerializer,
    open_checkpointer,
)
from core.memory.crypto import (
    DecryptionError,
    Envelope,
    KeyRing,
    KeyUnavailableError,
    LocalKeyProvider,
)
from core.memory.service import MemoryService
from core.memory.threads import ThreadService
from core.memory.types import Caller, MemoryNotFoundError, MemoryProposal
from core.permissions.errors import PermissionDeniedError
from core.permissions.runtime import RunContext
from database.schemas import AgentThread, MemoryAccessLog, MemoryMode, MemoryRecord
from tests.integration.conftest import build_service
from tests.integration.helpers import SpyEmbedder, runnable_agent, scan_database

RW = MemoryMode.READ_WRITE
CP_MARK = "CHECKPOINT-SECRET-MARKER-9137"
RAW = "Sam says the vault code is 90210 and to meet at the old pier at midnight, bring the signed paperwork."
SUMMARY = "Contact proposed a late meeting at a waterfront location and wants documents brought"


class GState(TypedDict):
    text: str
    n: int
    flag: bool
    ratio: float
    nothing: str | None
    items: list[str]
    nested: dict[str, Any]


def _step(s: Any) -> Any:
    return {
        "text": s["text"] + " " + CP_MARK,
        "n": s["n"] + 1,
        "flag": True,
        "ratio": 0.25,
        "nothing": None,
        "items": [*s["items"], CP_MARK],
        "nested": {"k": CP_MARK, "deep": [{"x": CP_MARK}]},
    }


def graph(saver: Any) -> Any:
    g = StateGraph(GState)
    g.add_node("a", _step)  # type: ignore[call-overload]  # LangGraph overloads reject plain callables
    g.add_node("b", _step)  # type: ignore[call-overload]
    g.set_entry_point("a")
    g.add_edge("a", "b")
    g.add_edge("b", END)
    return g.compile(checkpointer=saver)


INITIAL: GState = {
    "text": "hello",
    "n": 0,
    "flag": False,
    "ratio": 0.0,
    "nothing": None,
    "items": [],
    "nested": {},
}


async def run(saver: Any, thread_id: str) -> Any:
    app = graph(saver)
    await app.ainvoke(INITIAL, {"configurable": {"thread_id": thread_id}})
    return app


async def lg_rows(session: AsyncSession, thread_id: str) -> dict[str, int]:
    out = {}
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        out[table] = (
            await session.execute(
                text(f"SELECT count(*) FROM lg_checkpoints.{table} WHERE thread_id = :t"),
                {"t": thread_id},
            )
        ).scalar_one()
    return out


# --- encryption ------------------------------------------------------------------------------------------


async def test_state_of_every_primitive_type_survives_the_encrypted_roundtrip(
    saver: EncryptedPostgresSaver,
) -> None:
    app = await run(saver, "t-roundtrip")
    state = await app.aget_state({"configurable": {"thread_id": "t-roundtrip"}})
    v = state.values
    assert v["n"] == 2 and v["flag"] is True and v["ratio"] == 0.25 and v["nothing"] is None
    assert (
        v["text"] == f"hello {CP_MARK} {CP_MARK}"
        and v["items"] == [CP_MARK, CP_MARK]
        and v["nested"]["deep"] == [{"x": CP_MARK}]
    )
    history = [
        s async for s in app.aget_state_history({"configurable": {"thread_id": "t-roundtrip"}})
    ]
    assert len(history) >= 3 and all(
        isinstance(h.values.get("n", 0), int) for h in history
    )  # every old checkpoint decrypts


async def test_no_checkpoint_plaintext_exists_in_any_table_including_inline_primitives(
    saver: EncryptedPostgresSaver, migrated_url: URL
) -> None:
    await run(saver, "t-scan")
    # Without the boxing fix, `text` (a plain str) is written INLINE as JSON into checkpoints.checkpoint.
    assert scan_database(migrated_url, {"m": CP_MARK.encode(), "hello": b'"hello'}) == {
        "m": [],
        "hello": [],
    }


async def test_checkpoint_tables_live_in_their_own_schema_and_never_in_public(
    saver: EncryptedPostgresSaver, session: AsyncSession
) -> None:
    rows = (
        await session.execute(
            text(
                "SELECT table_schema, table_name FROM information_schema.tables WHERE table_name LIKE 'checkpoint%'"
            )
        )
    ).all()
    assert rows and {r[0] for r in rows} == {"lg_checkpoints"}
    assert {r[1] for r in rows} >= {
        "checkpoints",
        "checkpoint_blobs",
        "checkpoint_writes",
        "checkpoint_migrations",
    }


async def test_unencrypted_checkpoint_data_is_refused_not_deserialised(envelope: Envelope) -> None:
    ser = StrictEncryptedSerializer(
        __import__("core.memory.checkpoint", fromlist=["EnvelopeCipher"]).EnvelopeCipher(envelope)
    )
    typ, blob = ser.dumps_typed({"a": 1})
    assert typ.endswith("+abu1") and ser.loads_typed((typ, blob)) == {"a": 1}
    with pytest.raises(DecryptionError, match="unencrypted"):
        ser.loads_typed(
            ("msgpack", b"\x81\xa1a\x01")
        )  # a plaintext row planted by someone with DB write access
    with pytest.raises(DecryptionError):
        ser.loads_typed((typ.replace("abu1", "other"), blob))


async def test_tampered_or_wrongly_keyed_checkpoints_cannot_be_read(
    saver: EncryptedPostgresSaver, session: AsyncSession, migrated_url: URL
) -> None:
    app = await run(saver, "t-tamper")
    cfg = {"configurable": {"thread_id": "t-tamper"}}
    dsn = migrated_url.render_as_string(hide_password=False)
    for ring_id, error in (("env-1", DecryptionError), ("unknown-id", KeyUnavailableError)):
        other = Envelope(LocalKeyProvider(KeyRing(ring_id, {ring_id: b"x" * 32})))
        pool, wrong = await open_checkpointer(dsn, other)
        try:
            with pytest.raises(
                error
            ):  # same key id/different key fails auth; unknown key id fails closed
                await graph(wrong).aget_state(cfg)
        finally:
            await pool.close()
    await session.execute(
        text(
            "UPDATE lg_checkpoints.checkpoint_blobs SET blob = set_byte(blob, length(blob) - 2, 9) WHERE thread_id = 't-tamper' AND type <> 'empty'"
        )
    )
    await session.commit()
    with pytest.raises(DecryptionError):
        await app.aget_state(cfg)


async def test_state_may_not_use_the_reserved_box_key(saver: EncryptedPostgresSaver) -> None:
    class S(TypedDict):
        v: dict[str, Any]

    def reserved_key_node(s: Any) -> Any:
        return {"v": {BOX_KEY: 1}}

    g = StateGraph(S)
    g.add_node("a", reserved_key_node)  # type: ignore[call-overload]
    g.set_entry_point("a")
    g.add_edge("a", END)
    with pytest.raises(ValueError, match="reserved key"):
        await g.compile(checkpointer=saver).ainvoke(
            {"v": {}}, {"configurable": {"thread_id": "t-reserved"}}
        )


# --- ownership -----------------------------------------------------------------------------------------------


async def test_threads_are_owned_by_one_user_and_one_agent(
    session: AsyncSession,
    gov: Governance,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    alice, bob = two_users
    threads = ThreadService(session, saver)
    ctx = await runnable_agent(session, gov, "daily_coach_agent", user_id=alice)
    tid = await threads.create(ctx)
    await session.commit()
    assert (await threads.open(ctx, tid)) == {"configurable": {"thread_id": tid}}

    bob_ctx = await runnable_agent(
        session, gov, "messaging_agent", user_id=bob
    )  # another user (and another agent)
    with pytest.raises(MemoryNotFoundError):
        await threads.open(bob_ctx, tid)  # a thread of another user does not exist for you
    alice_other_agent = await runnable_agent(session, gov, "finance_agent", user_id=alice)
    with pytest.raises(PermissionDeniedError, match="another agent"):
        await threads.open(alice_other_agent, tid)  # same user, different agent: still denied
    with pytest.raises(MemoryNotFoundError):
        await threads.authorize("no-such-thread", user_id=alice)
    assert await threads.delete(bob, [tid]) == 0  # nor can another user delete it
    assert len(await threads.list_for_user(alice)) == 1 and await threads.list_for_user(bob) == []


async def test_expired_threads_cannot_be_opened_and_are_purged_with_all_their_checkpoints(
    session: AsyncSession,
    gov: Governance,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    alice, _ = two_users
    now = datetime(2026, 11, 24, tzinfo=UTC)
    clock = [now]
    threads = ThreadService(session, saver, retention_days=7, clock=lambda: clock[0])
    ctx = await runnable_agent(session, gov, "daily_coach_agent", user_id=alice)
    old, fresh = await threads.create(ctx), None
    await run(saver, old)
    clock[0] = now + timedelta(days=5)
    fresh = await threads.create(ctx)
    await run(saver, fresh)
    await session.commit()
    clock[0] = now + timedelta(days=8)  # `old` is 8 days old, `fresh` is 3
    with pytest.raises(MemoryNotFoundError, match="expired"):
        await threads.open(ctx, old)
    assert sum((await lg_rows(session, old)).values()) > 0
    assert await threads.purge_expired() == 1
    assert sum((await lg_rows(session, old)).values()) == 0  # history gone, not just the pointer
    assert sum((await lg_rows(session, fresh)).values()) > 0
    assert (await threads.open(ctx, fresh))["configurable"]["thread_id"] == fresh


# --- deletion reaches checkpoint history -----------------------------------------------------------------------------


async def messaging(
    session: AsyncSession, gov: Governance, svc: MemoryService, user: uuid.UUID
) -> tuple[AgentMemory, RunContext]:
    ctx = await runnable_agent(
        session,
        gov,
        "messaging_agent",
        user_id=user,
        memory_grants=(
            ("messaging_agent/instagram/c-1", RW),
            ("messaging_agent/instagram/c-2", RW),
        ),
    )
    return AgentMemory(
        svc, ctx, ThreadService(session, svc._threads._saver) if svc._threads else None
    ), ctx  # noqa: SLF001


async def test_deleting_a_memory_deletes_every_checkpoint_of_its_source_thread(
    mem_t: MemoryService,
    session: AsyncSession,
    gov: Governance,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
    migrated_url: URL,
) -> None:
    alice, bob = two_users
    am, ctx = await messaging(session, gov, mem_t, alice)
    threads = ThreadService(session, saver)
    source = await am.new_thread()
    unrelated = await threads.create(ctx)
    await run(saver, source)
    await run(saver, unrelated)
    bob_ctx = await runnable_agent(session, gov, "daily_coach_agent", user_id=bob)
    bobs = await threads.create(bob_ctx)
    await run(saver, bobs)
    r = await am.propose_from_third_party(
        platform="instagram",
        conversation_id="c-1",
        raw_text=RAW,
        summary=SUMMARY,
        source_thread_id=source,
    )
    await mem_t.approve(Caller.user(alice), r.id)
    await session.commit()

    before = await lg_rows(session, source)
    assert (
        before["checkpoints"] >= 3
        and before["checkpoint_blobs"] > 0
        and before["checkpoint_writes"] > 0
    )  # real history

    receipt = await mem_t.delete_record(Caller.user(alice), r.id)
    await session.commit()
    assert (receipt.records, receipt.threads) == (1, 1)
    assert (
        sum((await lg_rows(session, source)).values()) == 0
    )  # historical checkpoints, writes and blobs: all gone
    assert (
        await session.execute(select(AgentThread).where(AgentThread.thread_id == source))
    ).first() is None
    assert (
        sum((await lg_rows(session, unrelated)).values()) > 0
    )  # a different thread of the user is untouched
    assert sum((await lg_rows(session, bobs)).values()) > 0  # another user's thread is untouched
    assert (await session.execute(select(MemoryRecord))).first() is None
    assert scan_database(migrated_url, {"m": CP_MARK.encode()})["m"] == []


async def test_keeping_the_source_thread_is_an_explicit_choice(
    mem_t: MemoryService,
    session: AsyncSession,
    gov: Governance,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    alice, _ = two_users
    am, _ = await messaging(session, gov, mem_t, alice)
    source = await am.new_thread()
    await run(saver, source)
    r = await am.propose_from_third_party(
        platform="instagram",
        conversation_id="c-1",
        raw_text=RAW,
        summary=SUMMARY,
        source_thread_id=source,
    )
    await session.commit()
    receipt = await mem_t.delete_record(Caller.user(alice), r.id, purge_sources=False)
    assert receipt.threads == 0 and sum((await lg_rows(session, source)).values()) > 0


async def test_deletion_without_a_checkpointer_refuses_rather_than_leaving_checkpoints_behind(
    session: AsyncSession,
    gov: Governance,
    envelope: Envelope,
    embedder: SpyEmbedder,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    from core.memory.types import InvalidStateError

    alice, _ = two_users
    with_threads = build_service(
        session, envelope, embedder, gov, threads=ThreadService(session, saver)
    )
    am, _ = await messaging(session, gov, with_threads, alice)
    source = await am.new_thread()
    r = await am.propose_from_third_party(
        platform="instagram",
        conversation_id="c-1",
        raw_text=RAW,
        summary=SUMMARY,
        source_thread_id=source,
    )
    await session.commit()
    without = build_service(session, envelope, embedder, gov)  # misconfigured: no checkpointer
    with pytest.raises(InvalidStateError, match="no checkpointer"):
        await without.delete_record(Caller.user(alice), r.id)
    assert (
        await session.execute(select(MemoryRecord))
    ).first() is not None  # nothing was half-deleted


async def test_deleted_memories_cannot_be_found_by_vector_search_and_leave_no_embedding(
    mem: MemoryService, session: AsyncSession, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from database.schemas import MemoryEmbedding

    alice, _ = two_users
    user = Caller.user(alice)
    keep = await mem.propose(
        user,
        MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text="Enjoys hiking trails",
            source_kind="user_created",
            sensitivity="low",
            embed_text="enjoys hiking trails",
        ),
    )
    gone = await mem.propose(
        user,
        MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text="Likes vegetarian restaurants",
            source_kind="user_created",
            sensitivity="low",
            embed_text="likes vegetarian restaurants",
        ),
    )
    for r in (keep, gone):
        await mem.approve_embedding(user, r.id)
    assert [h.id for h in await mem.search(user, "vegetarian restaurants", ["user/n"], k=1)] == [
        gone.id
    ]
    receipt = await mem.delete_record(user, gone.id)
    assert (receipt.records, receipt.embeddings) == (1, 1)
    hits = await mem.search(user, "vegetarian restaurants", ["user/n"], k=5)
    assert [h.id for h in hits] == [keep.id]  # only the surviving memory can come back
    await session.commit()
    assert (
        await session.execute(
            text("SELECT count(*) FROM memory_embeddings WHERE memory_id = :i"), {"i": gone.id}
        )
    ).scalar_one() == 0
    assert (await session.execute(select(MemoryEmbedding))).scalars().all().__len__() == 1


async def test_deleting_a_memory_deletes_its_derived_summaries_and_every_older_version(
    mem: MemoryService,
    session: AsyncSession,
    two_users: tuple[uuid.UUID, uuid.UUID],
    migrated_url: URL,
) -> None:
    alice, _ = two_users
    user = Caller.user(alice)

    def up(text_: str, **kw: Any) -> MemoryProposal:
        return MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text=text_,
            source_kind="user_created",
            sensitivity="low",
            **kw,
        )

    v1 = await mem.propose(user, up("DELETE-ME version one", key="fact"))
    v2 = await mem.update(
        user, v1.id, up("DELETE-ME version two", key="fact")
    )  # v1 is now superseded history
    summary = await mem.propose(user, up("DELETE-ME derived summary", derived_from=v2.id))
    unrelated = await mem.propose(user, up("unrelated survivor"))
    await session.commit()
    assert len(await mem.list_memories(user, status="superseded")) == 1
    receipt = await mem.delete_record(user, v2.id)
    await session.commit()
    assert sorted(receipt.record_ids) == sorted(
        [v1.id, v2.id, summary.id]
    )  # original + history + derived summary
    assert [v.id for v in await mem.list_memories(user)] == [unrelated.id]
    assert scan_database(migrated_url, {"x": b"DELETE-ME"})["x"] == []


async def test_deletion_scrubs_access_log_references_and_records_an_audit_receipt_without_content(
    mem: MemoryService, session: AsyncSession, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    from database.schemas import AuditEvent

    alice, _ = two_users
    user = Caller.user(alice)
    r = await mem.propose(
        user,
        MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text="TOPSECRET-TEXT",
            source_kind="user_created",
            sensitivity="low",
        ),
    )
    await mem.get(user, r.id)
    receipt = await mem.delete_record(user, r.id)
    await session.commit()
    assert receipt.access_log_rows_scrubbed >= 2
    logs = (await session.execute(select(MemoryAccessLog))).scalars().all()
    assert logs and all(
        lg.memory_id is None for lg in logs
    )  # no dangling reference to what was deleted
    ev = (
        await session.execute(select(AuditEvent).where(AuditEvent.event_type == "memory.deleted"))
    ).scalar_one()
    assert ev.detail["records"] == 1 and "TOPSECRET" not in str(ev.detail)


async def test_namespace_deletion_respects_path_boundaries(
    mem: MemoryService, session: AsyncSession, two_users: tuple[uuid.UUID, uuid.UUID]
) -> None:
    alice, bob = two_users
    user = Caller.user(alice)

    def mk(ns: str) -> MemoryProposal:
        return MemoryProposal(
            namespace=ns,
            memory_type="semantic",
            text="x " + ns,
            source_kind="user_created",
            sensitivity="low",
        )

    for ns in ("user/a", "user/a/b", "user/ab", "user/a_c"):
        await mem.propose(user, mk(ns))
    bobs = await mem.propose(Caller.user(bob), mk("user/a"))
    receipt = await mem.delete_namespace(user, "user/a", purge_sources=False)
    assert (
        receipt.records == 2
    )  # user/a and user/a/b only - not user/ab, not user/a_c (LIKE wildcard), not Bob's
    assert {v.namespace for v in await mem.list_memories(user)} == {"user/ab", "user/a_c"}
    assert [v.id for v in await mem.list_memories(Caller.user(bob))] == [bobs.id]


async def test_erasure_removes_everything_of_the_user_and_nothing_of_anyone_else(
    mem_t: MemoryService,
    session: AsyncSession,
    gov: Governance,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
    migrated_url: URL,
) -> None:
    from database.schemas import MemoryEmbedding

    alice, bob = two_users
    am, ctx = await messaging(session, gov, mem_t, alice)
    threads = ThreadService(session, saver)
    linked, loose = (
        await am.new_thread(),
        await threads.create(ctx),
    )  # `loose` has no memory pointing at it
    await run(saver, linked)
    await run(saver, loose)
    r = await am.propose_from_third_party(
        platform="instagram",
        conversation_id="c-1",
        raw_text=RAW,
        summary=SUMMARY,
        source_thread_id=linked,
    )
    await mem_t.approve(Caller.user(alice), r.id)
    mine = await mem_t.propose(
        Caller.user(alice),
        MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text="ERASE-ME note",
            source_kind="user_created",
            sensitivity="low",
            embed_text="erase me note",
        ),
    )
    await mem_t.approve_embedding(Caller.user(alice), mine.id)
    bob_ctx = await runnable_agent(session, gov, "daily_coach_agent", user_id=bob)
    bob_thread = await threads.create(bob_ctx)
    await run(saver, bob_thread)
    bobs = await mem_t.propose(
        Caller.user(bob),
        MemoryProposal(
            namespace="user/n",
            memory_type="semantic",
            text="BOB-KEEPS-THIS",
            source_kind="user_created",
            sensitivity="low",
            embed_text="bob keeps this",
        ),
    )
    await mem_t.approve_embedding(Caller.user(bob), bobs.id)
    await session.commit()

    receipt = await mem_t.erase_user(Caller.user(alice))
    await session.commit()
    assert receipt.records == 2 and receipt.threads == 2
    assert (
        sum((await lg_rows(session, linked)).values())
        == sum((await lg_rows(session, loose)).values())
        == 0
    )
    left = (await session.execute(select(MemoryRecord))).scalars().all()
    assert [r_.user_id for r_ in left] == [bob]
    assert [
        e.user_id for e in (await session.execute(select(MemoryEmbedding))).scalars().all()
    ] == [bob]
    assert (
        await session.execute(select(MemoryAccessLog).where(MemoryAccessLog.user_id == alice))
    ).first() is None
    assert (
        sum((await lg_rows(session, bob_thread)).values()) > 0
        and (await mem_t.get(Caller.user(bob), bobs.id)).text == "BOB-KEEPS-THIS"
    )
    assert scan_database(migrated_url, {"a": b"ERASE-ME", "c": CP_MARK.encode()})["a"] == []


# --- retention ---------------------------------------------------------------------------------------------------------


async def test_retention_purges_proposals_third_party_summaries_episodes_and_superseded_history(
    session: AsyncSession,
    gov: Governance,
    envelope: Envelope,
    embedder: SpyEmbedder,
    saver: EncryptedPostgresSaver,
    two_users: tuple[uuid.UUID, uuid.UUID],
) -> None:
    alice, _ = two_users
    t0 = datetime(2026, 11, 24, tzinfo=UTC)
    clock = [t0]

    def now() -> datetime:
        return clock[0]

    svc = build_service(
        session,
        envelope,
        embedder,
        gov,
        clock=now,
        threads=ThreadService(session, saver, clock=now),
    )
    user = Caller.user(alice)
    am, _ = await messaging(session, gov, svc, alice)

    pending = await am.propose_from_third_party(
        platform="instagram", conversation_id="c-1", raw_text=RAW, summary=SUMMARY
    )  # 2-day TTL
    approved = await am.propose_from_third_party(
        platform="instagram",
        conversation_id="c-2",
        raw_text="other raw words here",
        summary="Contact mentioned a trip plan",
    )
    await svc.approve(user, approved.id)  # 90-day TTL after approval

    def mk(text_: str, t: str = "semantic", **kw: Any) -> MemoryProposal:
        return MemoryProposal(
            namespace="user/n",
            memory_type=t,
            text=text_,
            source_kind="user_created",
            sensitivity="low",
            **kw,
        )

    episode = await svc.propose(user, mk("an episode", t="episodic"))  # 180 days
    forever = await svc.propose(user, mk("a stable fact"))  # no expiry
    v1 = await svc.propose(user, mk("old value", key="k"))
    await svc.update(user, v1.id, mk("new value", key="k"))  # v1 superseded: 30 days
    await session.commit()

    async def alive() -> set[uuid.UUID]:
        await session.commit()
        return set((await session.execute(select(MemoryRecord.id))).scalars())

    assert await svc.purge_expired() == {
        "records": 0,
        "embeddings": 0,
        "threads": 0,
    }  # nothing is due yet
    clock[0] = t0 + timedelta(days=3)
    assert (await svc.purge_expired())[
        "records"
    ] == 1 and pending.id not in await alive()  # unapproved proposal
    clock[0] = t0 + timedelta(days=31)
    assert (await svc.purge_expired())[
        "records"
    ] == 1 and v1.id not in await alive()  # superseded history
    clock[0] = t0 + timedelta(days=91)
    assert (await svc.purge_expired())[
        "records"
    ] == 1 and approved.id not in await alive()  # third-party summary
    clock[0] = t0 + timedelta(days=181)
    assert (await svc.purge_expired())[
        "records"
    ] == 1 and episode.id not in await alive()  # episodic
    clock[0] = t0 + timedelta(days=3000)
    assert (await svc.purge_expired())[
        "records"
    ] == 0 and forever.id in await alive()  # stable facts stay until deleted
    assert (await svc.purge_expired()) == {
        "records": 0,
        "embeddings": 0,
        "threads": 0,
    }  # idempotent
