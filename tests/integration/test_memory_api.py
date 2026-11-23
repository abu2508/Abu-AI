"""The user's Memory Manager over HTTP: ownership, approval workflow, deletion receipts, erasure,
kill-switch behaviour, and what the API must never return."""

import json
import uuid

import pytest
from sqlalchemy import select, text

from core.audit.service import ADMIN
from core.governance import killswitch
from core.governance.service import Governance
from core.memory.agent import AgentMemory
from core.memory.crypto import Envelope
from core.memory.types import MemoryProposal
from database.schemas import MemoryMode, MemoryRecord
from tests.integration.conftest import Api, build_service
from tests.integration.helpers import Device, SpyEmbedder, enroll_device, make_user, runnable_agent

RW = MemoryMode.READ_WRITE
RAW = "Hey it is Sam, the pier meeting is at midnight, bring the signed paperwork and the vault code 90210."


def body(**kw: object) -> dict[str, object]:
    base: dict[str, object] = {
        "namespace": "user/profile",
        "memory_type": "semantic",
        "text": "Likes vegetarian restaurants",
    }
    base.update(kw)
    return base


async def agent_proposals(
    api: Api, device: Device, gov: Governance, envelope: Envelope, embedder: SpyEmbedder
) -> list[uuid.UUID]:
    """An agent (via the service, as the orchestrator will) leaves pending proposals for the user."""
    async with api.sessions() as s:
        svc = build_service(s, envelope, embedder, gov)
        ctx = await runnable_agent(
            s,
            gov,
            "messaging_agent",
            user_id=device.user_id,
            memory_grants=(("messaging_agent/instagram/c-1", RW), ("user/profile", RW)),
        )
        am = AgentMemory(svc, ctx)
        a = await am.propose_from_third_party(
            platform="instagram",
            conversation_id="c-1",
            raw_text=RAW,
            summary="Contact wants a late waterfront meeting",
        )
        b = await am.propose(
            MemoryProposal(
                namespace="user/profile",
                memory_type="procedural",
                text="Always reply briefly",
                source_kind="agent_inference",
            )
        )
        await s.commit()
    return [a.id, b.id]


async def test_create_get_list_and_response_shape(api: Api, device: Device) -> None:
    r = await api.client.post("/v1/memory", json=body(key="food"), headers=device.headers)
    assert r.status_code == 201
    m = r.json()
    assert (m["status"], m["source_kind"], m["created_by"], m["confidence"], m["sensitivity"]) == (
        "active",
        "user_created",
        "user",
        1.0,
        "personal",
    )
    assert (
        m["confirmed_at"] is not None
        and m["text"] == "Likes vegetarian restaurants"
        and m["embedding_state"] == "none"
    )
    assert not (
        {"embedding", "vector", "content_enc", "embed_text_enc"} & set(m)
    )  # nothing binary or vector-like is ever returned
    assert (await api.client.get(f"/v1/memory/{m['id']}", headers=device.headers)).json()[
        "text"
    ] == m["text"]
    listing = (
        await api.client.get("/v1/memory?namespace=user/profile", headers=device.headers)
    ).json()
    assert [x["id"] for x in listing] == [m["id"]]


async def test_user_created_memory_can_be_embedded_only_with_a_redacted_summary(
    api: Api, device: Device
) -> None:
    ok = await api.client.post(
        "/v1/memory",
        json=body(allow_embedding=True, embed_text="likes vegetarian restaurants"),
        headers=device.headers,
    )
    assert ok.status_code == 201 and ok.json()["embedding_state"] == "embedded"
    pii = await api.client.post(
        "/v1/memory",
        json=body(text="Another", allow_embedding=True, embed_text="mail me at a@b.com"),
        headers=device.headers,
    )
    assert (
        pii.status_code == 422
        and "personal identifiers" in pii.json()["detail"]
        and "a@b.com" not in pii.text
    )
    none = await api.client.post(
        "/v1/memory", json=body(text="Third", allow_embedding=True), headers=device.headers
    )
    assert none.status_code == 422  # embedding with nothing approved to embed
    n = (await api.client.get("/v1/memory", headers=device.headers)).json()
    assert len(n) == 1  # the failed requests created nothing


@pytest.mark.parametrize(
    ("override", "code"),
    [
        ({"namespace": "someone_elses_agent/x"}, 422),
        ({"namespace": "Bad Space"}, 422),
        ({"namespace": "user/../x"}, 422),
        ({"text": ""}, 422),
        ({"text": "x" * 4001}, 422),
        ({"memory_type": "weird"}, 422),
        ({"unknown": 1}, 422),
        ({"memory_type": "structured", "text": None}, 422),
        ({"text": "bad \x00 char"}, 422),
        ({"retention_days": 0}, 422),
    ],
)
async def test_invalid_creation_requests_are_rejected(
    api: Api, device: Device, override: dict[str, object], code: int
) -> None:
    r = await api.client.post("/v1/memory", json=body(**override), headers=device.headers)
    assert r.status_code == code
    assert (await api.client.get("/v1/memory", headers=device.headers)).json() == []


async def test_structured_memory_over_the_api_is_schema_validated(api: Api, device: Device) -> None:
    ok = await api.client.post(
        "/v1/memory",
        json={
            "namespace": "user/settings",
            "memory_type": "structured",
            "schema_name": "preference",
            "key": "units",
            "data": {"value": "metric"},
        },
        headers=device.headers,
    )
    assert ok.status_code == 201 and ok.json()["data"] == {"value": "metric"}
    for bad in ({"value": {"x": 1}}, {"value": "m", "x": 1}, {}):
        r = await api.client.post(
            "/v1/memory",
            json={
                "namespace": "user/settings",
                "memory_type": "structured",
                "schema_name": "preference",
                "key": "u2",
                "data": bad,
            },
            headers=device.headers,
        )
        assert r.status_code == 422


async def test_users_are_fully_isolated_over_the_api(api: Api, device: Device) -> None:
    mine = (
        await api.client.post(
            "/v1/memory", json=body(text="MINE-ONLY-FACT"), headers=device.headers
        )
    ).json()
    other = await enroll_device(
        api.sessions, api.settings, api.client, await make_user(api.sessions, "Other"), "OtherPhone"
    )
    h = other.headers
    assert (await api.client.get("/v1/memory", headers=h)).json() == []
    for method, path in (
        ("GET", f"/v1/memory/{mine['id']}"),
        ("PUT", f"/v1/memory/{mine['id']}"),
        ("DELETE", f"/v1/memory/{mine['id']}"),
        ("POST", f"/v1/memory/{mine['id']}/approve"),
        ("POST", f"/v1/memory/{mine['id']}/reject"),
    ):
        resp = await api.client.request(
            method, path, headers=h, json={"text": "hijack"} if method == "PUT" else {}
        )
        assert resp.status_code in (404, 409), (
            method,
            path,
            resp.status_code,
        )  # 409 only because mine is already active
        assert "MINE-ONLY" not in resp.text
    assert (
        await api.client.get(f"/v1/memory/{uuid.uuid4()}", headers=h)
    ).status_code == 404  # same as another user's id
    assert (await api.client.get(f"/v1/memory/{mine['id']}", headers=device.headers)).json()[
        "text"
    ] == "MINE-ONLY-FACT"
    their_log = (await api.client.get("/v1/memory/access-log", headers=h)).json()
    assert all(
        x["actor_id"] == str(other.user_id) and x["memory_id"] != mine["id"] for x in their_log
    )  # only their own actions
    assert (await api.client.delete("/v1/memory?namespace=user/profile", headers=h)).json()[
        "records"
    ] == 0


async def test_approving_and_rejecting_agent_proposals(
    api: Api, device: Device, gov: Governance, envelope: Envelope, embedder: SpyEmbedder
) -> None:
    third_party, rule = await agent_proposals(api, device, gov, envelope, embedder)
    pending = (await api.client.get("/v1/memory/proposals", headers=device.headers)).json()
    assert {p["id"] for p in pending} == {str(third_party), str(rule)} and all(
        p["status"] == "proposed" for p in pending
    )
    assert RAW[:30] not in json.dumps(pending) and "90210" not in json.dumps(
        pending
    )  # only the summary ever existed

    ok = await api.client.post(
        f"/v1/memory/{rule}/approve",
        json={"text": "Reply briefly and politely"},
        headers=device.headers,
    )
    assert (
        ok.status_code == 200
        and ok.json()["status"] == "active"
        and ok.json()["text"] == "Reply briefly and politely"
    )
    assert (
        await api.client.post(f"/v1/memory/{rule}/approve", json={}, headers=device.headers)
    ).status_code == 409  # already active
    assert (
        await api.client.post(f"/v1/memory/{third_party}/reject", headers=device.headers)
    ).status_code == 204
    assert (
        await api.client.get(f"/v1/memory/{third_party}", headers=device.headers)
    ).status_code == 404  # rejection DELETES it
    assert (
        await api.client.post(f"/v1/memory/{rule}/reject", headers=device.headers)
    ).status_code == 409  # stored memory: use delete


async def test_updating_replaces_the_fact_and_keeps_history_only_briefly(
    api: Api, device: Device
) -> None:
    m = (
        await api.client.post(
            "/v1/memory", json=body(text="Home city is Pune", key="home"), headers=device.headers
        )
    ).json()
    upd = await api.client.put(
        f"/v1/memory/{m['id']}", json={"text": "Home city is Mumbai"}, headers=device.headers
    )
    assert (
        upd.status_code == 200
        and upd.json()["text"] == "Home city is Mumbai"
        and upd.json()["id"] != m["id"]
    )
    active = (await api.client.get("/v1/memory?status=active", headers=device.headers)).json()
    assert [a["text"] for a in active] == ["Home city is Mumbai"]
    old = (await api.client.get("/v1/memory?status=superseded", headers=device.headers)).json()
    assert [o["text"] for o in old] == ["Home city is Pune"] and old[0][
        "retention_expires_at"
    ] is not None


async def test_delete_returns_a_receipt_and_removes_embeddings(api: Api, device: Device) -> None:
    m = (
        await api.client.post(
            "/v1/memory",
            json=body(allow_embedding=True, embed_text="likes vegetarian restaurants"),
            headers=device.headers,
        )
    ).json()
    receipt = (await api.client.delete(f"/v1/memory/{m['id']}", headers=device.headers)).json()
    assert receipt == {
        "records": 1,
        "embeddings": 1,
        "threads": 0,
        "access_log_rows_scrubbed": receipt["access_log_rows_scrubbed"],
    }
    assert (
        await api.client.get(f"/v1/memory/{m['id']}", headers=device.headers)
    ).status_code == 404
    async with api.sessions() as s:
        assert (await s.execute(text("SELECT count(*) FROM memory_embeddings"))).scalar_one() == 0
        assert (await s.execute(select(MemoryRecord))).first() is None


async def test_namespace_deletion_and_full_erasure_need_explicit_input(
    api: Api, device: Device
) -> None:
    for ns in ("user/a", "user/b"):
        await api.client.post(
            "/v1/memory", json=body(namespace=ns, text=f"fact in {ns}"), headers=device.headers
        )
    assert (
        await api.client.delete("/v1/memory", headers=device.headers)
    ).status_code == 422  # a namespace is REQUIRED
    assert (await api.client.delete("/v1/memory?namespace=user/a", headers=device.headers)).json()[
        "records"
    ] == 1
    assert (
        await api.client.post("/v1/memory/erase", json={}, headers=device.headers)
    ).status_code == 422
    assert (
        await api.client.post("/v1/memory/erase", json={"confirm": "yes"}, headers=device.headers)
    ).status_code == 422
    assert (
        len((await api.client.get("/v1/memory", headers=device.headers)).json()) == 1
    )  # nothing was erased by the failures
    done = await api.client.post(
        "/v1/memory/erase", json={"confirm": "ERASE ALL MY MEMORY"}, headers=device.headers
    )
    assert done.status_code == 200 and done.json()["records"] == 1
    assert (await api.client.get("/v1/memory", headers=device.headers)).json() == []


async def test_the_users_memory_manager_keeps_working_while_the_kill_switch_is_active(
    api: Api, device: Device
) -> None:
    m = (await api.client.post("/v1/memory", json=body(), headers=device.headers)).json()
    await killswitch.activate(api.sessions, ADMIN, "incident")
    assert (
        await api.client.get(f"/v1/memory/{m['id']}", headers=device.headers)
    ).status_code == 200
    assert (
        await api.client.post(
            "/v1/memory", json=body(text="written during the incident"), headers=device.headers
        )
    ).status_code == 201
    assert (
        await api.client.delete(f"/v1/memory/{m['id']}", headers=device.headers)
    ).status_code == 200  # the owner can still delete
    assert (
        await api.client.post(
            "/v1/memory/erase", json={"confirm": "ERASE ALL MY MEMORY"}, headers=device.headers
        )
    ).status_code == 200


async def test_access_log_endpoint_lists_actions_without_content(api: Api, device: Device) -> None:
    m = (
        await api.client.post(
            "/v1/memory", json=body(text="LOGGED-SECRET-TEXT"), headers=device.headers
        )
    ).json()
    await api.client.get(f"/v1/memory/{m['id']}", headers=device.headers)
    log = (await api.client.get("/v1/memory/access-log", headers=device.headers)).json()
    assert [(x["actor_type"], x["action"]) for x in reversed(log)][:2] == [
        ("user", "create"),
        ("user", "read"),
    ]
    assert "LOGGED-SECRET-TEXT" not in json.dumps(log)


async def test_threads_can_be_listed_and_deleted_by_their_owner_only(
    api: Api, device: Device, gov: Governance
) -> None:
    from core.memory.threads import ThreadService

    async with api.sessions() as s:
        ctx = await runnable_agent(s, gov, "daily_coach_agent", user_id=device.user_id)
        tid = await ThreadService(s, api.app.state.memory.saver).create(ctx)
        await s.commit()
    listing = (await api.client.get("/v1/memory/threads", headers=device.headers)).json()
    assert [t["thread_id"] for t in listing] == [tid] and listing[0][
        "agent_id"
    ] == "daily_coach_agent"
    other = await enroll_device(
        api.sessions, api.settings, api.client, await make_user(api.sessions, "Other"), "O"
    )
    assert (
        await api.client.delete(f"/v1/memory/threads/{tid}", headers=other.headers)
    ).status_code == 404
    assert (
        await api.client.delete(f"/v1/memory/threads/{tid}", headers=device.headers)
    ).status_code == 204
    assert (await api.client.get("/v1/memory/threads", headers=device.headers)).json() == []


async def test_errors_never_echo_memory_content(api: Api, device: Device) -> None:
    private = "MY-PRIVATE-DETAIL-31337"
    r = await api.client.post(
        "/v1/memory", json=body(text=f"{private} \x00"), headers=device.headers
    )
    assert r.status_code == 422 and private not in r.text
    r = await api.client.post(
        "/v1/memory",
        json=body(text=private, embed_text=f"{private} jane@x.com", allow_embedding=True),
        headers=device.headers,
    )
    assert r.status_code == 422 and private not in r.text and "jane@x.com" not in r.text
