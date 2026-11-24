# ruff: noqa: F811
# mypy: disable-error-code="no-untyped-call,func-returns-value,no-untyped-def,arg-type,no-any-return,index"
"""B1: the batch inbox endpoint. Privacy rule: everything derived from message CONTENT is available only for
conversations explicitly shared with Abu AI, enforced server-side on every request."""

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import event, text

from core.audit.service import ADMIN
from core.governance import killswitch
from domain.common.crypto import FieldCipher
from domain.messaging.service import MessagingError, MessagingService
from tests.integration.conftest import Api
from tests.integration.helpers import HASH, Device, enroll_device, make_user
from tests.integration.test_orchestrator import booted, device_for, no_external_network  # noqa: F401

NOW = datetime.now(UTC)
MARKER_TEXT = "inbox-secret-token-QRS"


def j(last_sender: str, last_text: str, hours_ago: float = 3.0, n_before: int = 1) -> str:
    base = NOW - timedelta(hours=hours_ago, minutes=30)
    msgs = [
        {
            "sender": "me" if i % 2 else "them",
            "text": f"{MARKER_TEXT} earlier {i}",
            "sent_at": (base + timedelta(minutes=i * 7)).isoformat(),
        }
        for i in range(n_before)
    ]
    msgs.append(
        {
            "sender": last_sender,
            "text": last_text,
            "sent_at": (NOW - timedelta(hours=hours_ago)).isoformat(),
        }
    )
    return json.dumps(msgs)


async def imp(
    api: Api,
    dev: Device,
    text_: str,
    *,
    share: bool = True,
    fmt: str = "json",
    title: str = "Asha",
    platform: str = "whatsapp",
) -> dict[str, Any]:
    r = await api.client.post(
        "/v1/messaging/conversations/import",
        json={
            "platform": platform,
            "format": fmt,
            "text": text_,
            "title": title,
            "share_with_assistant": share,
        },
        headers=dev.headers,
    )
    assert r.status_code == 201, r.text
    return r.json()


async def inbox(api: Api, dev: Device, **params):
    r = await api.client.get("/v1/messaging/inbox", params=params, headers=dev.headers)
    assert r.status_code == 200, r.text
    return r.json(), r


def row(items: list[dict[str, Any]], cid: str) -> dict[str, Any]:
    return next(i for i in items if i["id"] == cid)


async def dev2(api: Api, name: str = "B") -> Device:
    return await enroll_device(
        api.sessions, api.settings, api.client, await make_user(api.sessions, name)
    )


class StatementCounter:
    def __init__(self, api: Api) -> None:
        self.engine = api.app.state.engine.sync_engine
        self.statements: list[str] = []

    def _on(self, conn, cursor, statement, params, context, executemany) -> None:  # noqa: ANN001
        self.statements.append(statement)

    def __enter__(self) -> "StatementCounter":
        event.listen(self.engine, "before_cursor_execute", self._on)
        return self

    def __exit__(self, *a: object) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on)


# ---- access, validation, isolation -----------------------------------------------------------------------------
async def test_requires_authentication_and_validates_paging(api: Api, device: Device) -> None:
    assert (await api.client.get("/v1/messaging/inbox")).status_code == 401
    assert (
        await api.client.get("/v1/messaging/inbox", headers={"Authorization": "Bearer x"})
    ).status_code == 401
    for bad in ({"limit": 0}, {"limit": 101}, {"offset": -1}, {"limit": "x"}, {"offset": 100_001}):
        assert (
            await api.client.get("/v1/messaging/inbox", params=bad, headers=device.headers)
        ).status_code == 422, bad
    assert (
        await api.client.get(
            "/v1/messaging/inbox", params={"limit": 100, "offset": 0}, headers=device.headers
        )
    ).status_code == 200


async def test_users_only_see_their_own_inbox(api: Api, device: Device) -> None:
    mine = await imp(api, device, j("them", "Are you free tomorrow?"))
    other = await dev2(api)
    theirs, _ = await inbox(api, other)
    assert theirs == []
    other_conv = await imp(api, other, j("them", "Dinner?"), title="Other")
    mine_items, _ = await inbox(api, device)
    assert [i["id"] for i in mine_items] == [mine["id"]] and other_conv["id"] not in json.dumps(
        mine_items
    )


# ---- shape and privacy ------------------------------------------------------------------------------------------
async def test_no_message_text_is_ever_returned(api: Api, device: Device) -> None:
    await imp(api, device, j("them", f"{MARKER_TEXT} are you free tomorrow for dinner?"))
    await imp(
        api, device, j("them", f"{MARKER_TEXT} unshared message"), share=False, title="Hidden"
    )
    items, resp = await inbox(api, device)
    assert MARKER_TEXT not in resp.text and len(items) == 2
    allowed = {
        "id",
        "slug",
        "platform",
        "title",
        "contact_id",
        "shared_with_assistant",
        "message_count",
        "last_message_at",
        "last_sender",
        "needs_reply",
        "waiting_hours",
        "timestamps_reliable",
        "last_message_intent",
        "flagged_messages",
    }
    assert all(set(i) == allowed for i in items)  # exactly the documented fields, nothing else


async def test_content_derived_fields_exist_only_for_shared_conversations(
    api: Api, device: Device
) -> None:
    shared = await imp(api, device, j("them", "Are you free tomorrow for dinner?"), title="Shared")
    unshared = await imp(
        api, device, j("them", "Are you free tomorrow for dinner?"), share=False, title="Unshared"
    )
    items, _ = await inbox(api, device)
    s, u = row(items, shared["id"]), row(items, unshared["id"])
    assert s["last_message_intent"] in ("invitation", "scheduling") and s["flagged_messages"] == 0
    assert u["last_message_intent"] is None and u["flagged_messages"] is None  # null, not "unknown"
    # metadata-only status is still available for both: it needs no message content
    assert (
        s["needs_reply"] is True
        and u["needs_reply"] is True
        and u["last_sender"] == "them"
        and u["waiting_hours"] is not None
    )


async def test_unshared_message_bodies_are_never_loaded_or_decrypted(
    api: Api, device: Device, monkeypatch
) -> None:
    await imp(api, device, j("them", "Are you free tomorrow?", n_before=4), share=False)
    await imp(
        api, device, j("me", "yes sure", n_before=4), share=True, title="Mine last"
    )  # shared but I spoke last: no intent needed
    opened: list[str] = []
    orig = FieldCipher.open

    def spy(self, user_id, row_id, purpose, blob):
        opened.append(purpose)
        return orig(self, user_id, row_id, purpose, blob)

    monkeypatch.setattr(FieldCipher, "open", spy)
    with StatementCounter(api) as cnt:
        await inbox(api, device)
    assert "msg.body" not in opened  # no body decrypted for either conversation
    assert not any(
        "body_enc" in s
        and "msg_messages" in s
        and "FROM msg_messages" in s
        and "latest" in s.lower()
        for s in cnt.statements
    )
    # the only statement allowed to select body_enc is the shared+them one, and here nothing qualifies, so none ran
    assert not any("msg_messages.body_enc" in s for s in cnt.statements)


async def test_sharing_changes_take_effect_immediately_server_side(
    booted: Api, monkeypatch
) -> None:
    """The key security test: permission changes apply on the very next request; they are not a UI concern."""
    api = booted
    dev = await device_for(api)
    c = await imp(api, dev, j("them", "Are you free tomorrow for dinner?"))
    cid = c["id"]
    opened: list[str] = []
    orig = FieldCipher.open

    def spy(self, user_id, row_id, purpose, blob):
        opened.append(purpose)
        return orig(self, user_id, row_id, purpose, blob)

    monkeypatch.setattr(FieldCipher, "open", spy)

    async def analysis_via_service(for_agent: bool):
        async with api.sessions() as s:
            cipher = FieldCipher(api.app.state.memory.envelope, HASH)
            return await MessagingService(s, cipher).analysis(
                dev.user_id, uuid.UUID(cid), for_agent=for_agent
            )

    def patch(flag: bool):
        return api.client.patch(
            f"/v1/messaging/conversations/{cid}",
            json={"shared_with_assistant": flag},
            headers=dev.headers,
        )

    # 1) shared: the inbox exposes the content-derived intent, and the agent may analyse
    items, _ = await inbox(api, dev)
    assert row(items, cid)["last_message_intent"] is not None and "msg.body" in opened
    assert (await analysis_via_service(True))["message_count"] >= 1
    # 2) the user turns sharing OFF: the very next inbox call returns null and decrypts no body
    opened.clear()
    assert (await patch(False)).status_code == 200
    items, _ = await inbox(api, dev)
    r = row(items, cid)
    assert (
        r["last_message_intent"] is None
        and r["flagged_messages"] is None
        and r["shared_with_assistant"] is False
    )
    assert "msg.body" not in opened
    with pytest.raises(MessagingError) as ei:  # every other agent-facing path agrees
        await analysis_via_service(True)
    assert ei.value.code == "not_shared"
    resp = await api.client.post(
        "/v1/orchestrator/run",
        json={"message": "Help me reply", "conversation_id": cid},
        headers=dev.headers,
    )
    assert resp.json()["data"]["messaging.reply_suggestions"] == {"error": "not_shared"}
    # 3) sharing ON again: effective immediately
    assert (await patch(True)).status_code == 200
    items, _ = await inbox(api, dev)
    assert row(items, cid)["last_message_intent"] is not None
    assert (await analysis_via_service(True))["message_count"] >= 1


async def test_sharing_toggle_is_never_cached_across_users_or_requests(
    api: Api, device: Device
) -> None:
    other = await dev2(api)
    a = await imp(api, device, j("them", "Dinner tomorrow?"), title="A")
    b = await imp(api, other, j("them", "Dinner tomorrow?"), title="B")
    await api.client.patch(
        f"/v1/messaging/conversations/{a['id']}",
        json={"shared_with_assistant": False},
        headers=device.headers,
    )
    ia, _ = await inbox(api, device)
    ib, _ = await inbox(api, other)
    assert (
        row(ia, a["id"])["last_message_intent"] is None
        and row(ib, b["id"])["last_message_intent"] is not None
    )  # one user's change affects only theirs


# ---- correctness vs analysis ---------------------------------------------------------------------------------------
async def test_inbox_agrees_with_analysis_for_shared_conversations(
    api: Api, device: Device
) -> None:
    cases = [
        ("them", "Are you free tomorrow for dinner?", 5.0),
        ("them", "Thanks so much!", 30.0),
        ("me", "On my way", 2.0),
        ("them", "I am so stressed today", 12.5),
    ]
    ids = []
    for k, (sender, text_, hours) in enumerate(cases):
        ids.append(
            (await imp(api, device, j(sender, text_, hours, n_before=2), title=f"T{k}"))["id"]
        )
    items, _ = await inbox(api, device)
    for cid in ids:
        a = (
            await api.client.get(
                f"/v1/messaging/conversations/{cid}/analysis", headers=device.headers
            )
        ).json()
        r = row(items, cid)
        assert r["needs_reply"] == a["needs_reply"] and r["last_sender"] == a["last_sender"]
        assert (
            r["last_message_intent"] == a["last_message_intent"]
            and r["waiting_hours"] == a["waiting_hours"]
        )


# ---- paging, ordering, bounded queries -----------------------------------------------------------------------------------
async def test_pagination_ordering_total_and_shared_only(api: Api, device: Device) -> None:
    ids = []
    for k in range(5):
        ids.append(
            (
                await imp(
                    api,
                    device,
                    j("them", "hello there", hours_ago=10 - k),
                    share=k != 2,
                    title=f"C{k}",
                )
            )["id"]
        )
    page1, r1 = await inbox(api, device, limit=2)
    page2, _ = await inbox(api, device, limit=2, offset=2)
    page3, _ = await inbox(api, device, limit=2, offset=4)
    assert r1.headers["x-total-count"] == "5" and [len(p) for p in (page1, page2, page3)] == [
        2,
        2,
        1,
    ]
    ordered = [i["id"] for i in (*page1, *page2, *page3)]
    assert (
        ordered == list(reversed(ids)) and len(set(ordered)) == 5
    )  # newest first, no duplicates across pages
    only, ro = await inbox(api, device, shared_only="true")
    assert ro.headers["x-total-count"] == "4" and ids[2] not in [i["id"] for i in only]
    empty, re_ = await inbox(api, device, offset=50)
    assert empty == [] and re_.headers["x-total-count"] == "5"


async def test_query_count_is_constant_not_per_row(api: Api, device: Device, monkeypatch) -> None:
    async def count_statements() -> int:
        with StatementCounter(api) as cnt:
            await inbox(api, device, limit=100)
        return len(cnt.statements)

    for k in range(3):
        await imp(api, device, j("them", "Are you free tomorrow?", n_before=3), title=f"S{k}")
    few = await count_statements()
    for k in range(27):
        await imp(
            api,
            device,
            j("them", "Are you free tomorrow?", n_before=3),
            share=k % 2 == 0,
            title=f"M{k}",
        )
    many = await count_statements()
    assert few == many  # 3 conversations or 30: the same number of SQL statements
    assert many <= 12  # authentication overhead + page, total, latest, bodies, flags
    opened: list[str] = []
    orig = FieldCipher.open
    monkeypatch.setattr(
        FieldCipher, "open", lambda self, u, r, p, b: (opened.append(p), orig(self, u, r, p, b))[1]
    )
    await inbox(api, device, limit=100)
    assert (
        opened.count("msg.body") <= 30 and opened.count("msg.body") == 17
    )  # shared (3 + 14) conversations whose last message is theirs


async def test_expired_messages_are_ignored(api: Api, device: Device) -> None:
    c = await imp(api, device, j("them", "Are you free tomorrow?"))
    async with api.sessions() as s, s.begin():
        await s.execute(text("UPDATE msg_messages SET expires_at = now() - interval '1 minute'"))
    items, _ = await inbox(api, device)
    r = row(items, c["id"])
    assert (
        r["last_sender"] is None
        and r["needs_reply"] is False
        and r["last_message_intent"] is None
        and r["waiting_hours"] is None
    )


async def test_unreliable_timestamps_never_report_waiting_time(api: Api, device: Device) -> None:
    labelled = await imp(
        api,
        device,
        "me: hi\nthem: hello there\nme: how are you\nthem: good, free tomorrow?",
        fmt="labelled",
        title="Pasted",
    )
    real = await imp(api, device, j("them", "Free tomorrow?"), title="Real")
    items, _ = await inbox(api, device)
    a, b = row(items, labelled["id"]), row(items, real["id"])
    assert (
        a["timestamps_reliable"] is False
        and a["waiting_hours"] is None
        and a["needs_reply"] is True
    )
    assert b["timestamps_reliable"] is True and b["waiting_hours"] is not None
    # appending a labelled paste to a reliable conversation makes it unreliable (synthetic times are now mixed in)
    await api.client.post(
        f"/v1/messaging/conversations/{real['id']}/messages",
        json={"format": "labelled", "text": "them: another one"},
        headers=device.headers,
    )
    items, _ = await inbox(api, device)
    assert (
        row(items, real["id"])["timestamps_reliable"] is False
        and row(items, real["id"])["waiting_hours"] is None
    )


async def test_inbox_is_an_own_data_route_and_survives_the_kill_switch(
    api: Api, device: Device
) -> None:
    await imp(api, device, j("them", "hello"))
    await killswitch.activate(api.sessions, ADMIN, "drill")
    items, _ = await inbox(api, device)
    assert len(items) == 1


async def test_no_side_effects_on_memory_or_embeddings(api: Api, device: Device) -> None:
    await imp(api, device, j("them", "Are you free tomorrow?"))
    await inbox(api, device)
    async with api.sessions() as s:
        for t in ("memory_records", "memory_embeddings", "executions"):
            assert (await s.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() == 0
