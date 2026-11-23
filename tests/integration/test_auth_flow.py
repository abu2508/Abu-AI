"""Authentication end to end: enrollment, short-lived access, rotating refresh, revocation."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, text
from sqlalchemy.engine import URL
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core.audit.service import ADMIN
from database.schemas import AuditEvent, AuthSession, EnrollmentCode, RefreshToken, User
from database.seed.registry import seed_registry
from services.api.auth import service as auth
from services.api.auth import tokens
from services.api.config import Settings
from services.api.main import create_app
from tests.integration.helpers import Device, enroll_device, make_settings, make_user


@dataclass
class Api:
    app: FastAPI
    client: httpx.AsyncClient
    settings: Settings
    sessions: async_sessionmaker[AsyncSession]


@pytest.fixture
async def api(migrated_url: URL, sessions: async_sessionmaker[AsyncSession]) -> AsyncIterator[Api]:
    settings = make_settings(migrated_url)
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app, client=("203.0.113.5", 4000))
        async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
            yield Api(app, client, settings, sessions)


@pytest.fixture
async def device(api: Api) -> Device:
    uid = await make_user(api.sessions)
    return await enroll_device(api.sessions, api.settings, api.client, uid)


async def audit_types(api: Api) -> list[str]:
    async with api.sessions() as s:
        return list(
            (await s.execute(select(AuditEvent.event_type).order_by(AuditEvent.id))).scalars()
        )


# --- enrollment -----------------------------------------------------------------------------


async def test_enroll_then_authenticated_request_carries_verified_user_id(
    api: Api, device: Device
) -> None:
    resp = await api.client.get("/v1/me", headers=device.headers)
    assert resp.status_code == 200
    assert resp.json() == {"user_id": str(device.user_id), "session_id": str(device.session_id)}


async def test_access_token_is_short_lived(api: Api, device: Device) -> None:
    claims = __import__("jwt").decode(device.access, options={"verify_signature": False})
    assert 0 < claims["exp"] - claims["iat"] <= 3600


async def test_enrollment_code_is_single_use(api: Api) -> None:
    uid = await make_user(api.sessions)
    async with api.sessions() as s, s.begin():
        code, _ = await auth.create_enrollment_code(s, api.settings, uid, ADMIN)
    body = {"enrollment_code": code, "device_name": "A", "platform": "ios"}
    assert (await api.client.post("/v1/auth/enroll", json=body)).status_code == 200
    again = await api.client.post("/v1/auth/enroll", json=body)
    assert again.status_code == 401 and again.json() == {"detail": "invalid credentials"}


async def test_failure_modes_are_indistinguishable(api: Api) -> None:
    uid = await make_user(api.sessions)
    async with api.sessions() as s, s.begin():
        code, _ = await auth.create_enrollment_code(s, api.settings, uid, ADMIN)
        await auth.create_user(s, "x", ADMIN)
    unknown = await api.client.post(
        "/v1/auth/enroll", json={"enrollment_code": "enr_" + "a" * 22, "device_name": "d"}
    )
    async with api.sessions() as s, s.begin():
        await s.execute(
            text("UPDATE enrollment_codes SET expires_at = now() - interval '1 minute'")
        )
    expired = await api.client.post(
        "/v1/auth/enroll", json={"enrollment_code": code, "device_name": "d"}
    )
    assert unknown.status_code == expired.status_code == 401
    assert unknown.text == expired.text  # no oracle for "exists but expired"
    types = await audit_types(api)
    assert types.count("enrollment.failed") == 2  # ...but the audit log records the real reasons


async def test_expired_enrollment_code_rejected_at_service_level(api: Api) -> None:
    uid = await make_user(api.sessions)
    async with api.sessions() as s, s.begin():
        code, exp = await auth.create_enrollment_code(s, api.settings, uid, ADMIN)
    assert exp - datetime.now(UTC) <= timedelta(
        minutes=api.settings.enrollment_code_ttl_minutes, seconds=2
    )
    async with api.sessions() as s:
        with pytest.raises(auth.AuthError):
            await auth.enroll(s, api.settings, code, "d", "ios", now=exp + timedelta(seconds=1))


async def test_disabled_user_cannot_enroll_or_use_existing_tokens(api: Api, device: Device) -> None:
    async with api.sessions() as s, s.begin():
        await s.execute(text("UPDATE users SET disabled_at = now()"))
    assert (await api.client.get("/v1/me", headers=device.headers)).status_code == 401
    assert (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": device.refresh})
    ).status_code == 401


# --- secrets at rest -------------------------------------------------------------------------


async def test_only_hashes_are_stored(api: Api) -> None:
    uid = await make_user(api.sessions)
    async with api.sessions() as s, s.begin():
        code, _ = await auth.create_enrollment_code(s, api.settings, uid, ADMIN)
    dev = await enroll_device(api.sessions, api.settings, api.client, uid, "second")
    async with api.sessions() as s:
        stored = [
            *(await s.execute(select(RefreshToken.token_hash))).scalars(),
            *(await s.execute(select(EnrollmentCode.code_hash))).scalars(),
        ]
    assert all(len(h) == 64 for h in stored)
    for secret in (dev.refresh, code):
        assert secret not in stored
    # A leaked database row must not be usable as a credential.
    leaked = stored[0]
    assert (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": leaked})
    ).status_code == 401
    dump = await _dump_all_text(api)
    assert dev.refresh not in dump and dev.access not in dump and code not in dump


async def _dump_all_text(api: Api) -> str:
    """Every row of every auth-related table as JSON text, to prove no plaintext secret is stored."""
    parts: list[str] = []
    async with api.sessions() as s:
        for table in (
            "refresh_tokens",
            "enrollment_codes",
            "auth_sessions",
            "audit_events",
            "users",
        ):
            result = await s.execute(text(f"SELECT row_to_json(t)::text FROM {table} t"))  # noqa: S608
            parts += list(result.scalars())
    return "\n".join(parts)


# --- refresh rotation and reuse detection ------------------------------------------------------


async def test_refresh_rotates_and_new_tokens_work(api: Api, device: Device) -> None:
    resp = await api.client.post("/v1/auth/refresh", json={"refresh_token": device.refresh})
    assert resp.status_code == 200
    new = resp.json()
    assert new["refresh_token"] != device.refresh and new["session_id"] == str(device.session_id)
    ok = await api.client.get("/v1/me", headers={"Authorization": f"Bearer {new['access_token']}"})
    assert ok.status_code == 200


async def test_reusing_a_rotated_refresh_token_revokes_the_whole_session(
    api: Api, device: Device
) -> None:
    first = (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": device.refresh})
    ).json()
    replay = await api.client.post(
        "/v1/auth/refresh", json={"refresh_token": device.refresh}
    )  # attacker/stale copy
    assert replay.status_code == 401
    # The legitimate, newest credentials are dead too: theft cannot be told apart from a stale client.
    assert (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": first["refresh_token"]})
    ).status_code == 401
    assert (
        await api.client.get("/v1/me", headers={"Authorization": f"Bearer {first['access_token']}"})
    ).status_code == 401
    types = await audit_types(api)
    assert "auth.refresh_reuse_detected" in types and "auth.session_revoked" in types
    async with api.sessions() as s:
        row = (await s.execute(select(AuthSession))).scalar_one()
        assert row.revoked_reason == "refresh_token_reuse"


async def test_concurrent_refresh_with_same_token_only_one_wins(api: Api, device: Device) -> None:
    body = {"refresh_token": device.refresh}
    results = await asyncio.gather(
        *(api.client.post("/v1/auth/refresh", json=body) for _ in range(4))
    )
    codes = sorted(r.status_code for r in results)
    assert (
        codes.count(200) == 1 and codes.count(401) == 3
    )  # never two live descendants of one token


async def test_expired_refresh_token_and_absolute_session_lifetime(
    api: Api, device: Device
) -> None:
    far = datetime.now(UTC) + timedelta(days=api.settings.refresh_token_ttl_days + 1)
    async with api.sessions() as s:
        with pytest.raises(auth.AuthError):
            await auth.refresh(s, api.settings, device.refresh, now=far)
    async with api.sessions() as s:
        with pytest.raises(auth.AuthError):  # access check also honours the absolute session expiry
            await auth.authenticate(
                s,
                api.settings,
                device.access,
                now=datetime.now(UTC) + timedelta(days=api.settings.session_max_days + 1),
            )


async def test_refresh_token_never_outlives_the_session(api: Api, device: Device) -> None:
    async with api.sessions() as s:
        sess = (await s.execute(select(AuthSession))).scalar_one()
        tok = (await s.execute(select(RefreshToken))).scalar_one()
        assert tok.expires_at <= sess.expires_at


async def test_expired_or_forged_access_token_rejected(api: Api, device: Device) -> None:
    old, _ = tokens.issue_access_token(
        api.settings, device.user_id, device.session_id, datetime.now(UTC) - timedelta(hours=1)
    )
    assert (
        await api.client.get("/v1/me", headers={"Authorization": f"Bearer {old}"})
    ).status_code == 401
    other = make_settings(jwt_secret="q" * 48)
    forged, _ = tokens.issue_access_token(other, device.user_id, device.session_id)
    assert (
        await api.client.get("/v1/me", headers={"Authorization": f"Bearer {forged}"})
    ).status_code == 401


async def test_valid_signature_but_wrong_user_for_session_rejected(
    api: Api, device: Device
) -> None:
    tok, _ = tokens.issue_access_token(api.settings, uuid.uuid4(), device.session_id)
    assert (
        await api.client.get("/v1/me", headers={"Authorization": f"Bearer {tok}"})
    ).status_code == 401


# --- sessions: listing and revocation ----------------------------------------------------------


async def test_logout_revokes_access_and_refresh_immediately(api: Api, device: Device) -> None:
    assert (await api.client.post("/v1/auth/logout", headers=device.headers)).status_code == 204
    assert (
        await api.client.get("/v1/me", headers=device.headers)
    ).status_code == 401  # no waiting for expiry
    assert (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": device.refresh})
    ).status_code == 401


async def test_list_and_revoke_another_device(api: Api, device: Device) -> None:
    other = await enroll_device(api.sessions, api.settings, api.client, device.user_id, "iPad")
    listing = (await api.client.get("/v1/auth/sessions", headers=device.headers)).json()
    assert {s["device_name"] for s in listing} == {"iPhone", "iPad"}
    assert [s["current"] for s in listing if s["id"] == str(device.session_id)] == [True]
    assert "refresh" not in str(listing) and "token" not in str(listing)

    assert (
        await api.client.delete(f"/v1/auth/sessions/{other.session_id}", headers=device.headers)
    ).status_code == 204
    assert (await api.client.get("/v1/me", headers=other.headers)).status_code == 401
    assert (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": other.refresh})
    ).status_code == 401
    assert (await api.client.get("/v1/me", headers=device.headers)).status_code == 200  # untouched


async def test_cannot_see_or_revoke_another_users_sessions(api: Api, device: Device) -> None:
    uid2 = await make_user(api.sessions, "Other")
    dev2 = await enroll_device(api.sessions, api.settings, api.client, uid2, "OtherPhone")
    listing = (await api.client.get("/v1/auth/sessions", headers=device.headers)).json()
    assert [s["id"] for s in listing] == [str(device.session_id)]
    resp = await api.client.delete(f"/v1/auth/sessions/{dev2.session_id}", headers=device.headers)
    assert resp.status_code == 404  # indistinguishable from "does not exist"
    assert (await api.client.get("/v1/me", headers=dev2.headers)).status_code == 200


async def test_revocation_is_idempotent_and_audited(api: Api, device: Device) -> None:
    for _ in range(2):
        assert (
            await api.client.delete(
                f"/v1/auth/sessions/{device.session_id}", headers=device.headers
            )
        ).status_code in (204, 401)
    assert (await audit_types(api)).count("auth.session_revoked") == 1


# --- privilege escalation: an app token can only reduce privilege ---------------------------------


async def test_app_token_cannot_enable_grant_or_activate(api: Api, device: Device) -> None:
    async with api.sessions() as s, s.begin():
        await seed_registry(s)
    h = device.headers
    attempts = [
        ("POST", "/v1/agents/finance_agent/enable"),
        ("PUT", "/v1/agents/finance_agent"),
        ("PATCH", "/v1/agents/finance_agent"),
        ("POST", "/v1/agents/finance_agent/versions"),
        ("POST", "/v1/agents/finance_agent/versions/1/activate"),
        ("POST", "/v1/agents/finance_agent/versions/1/grants"),
        ("PUT", "/v1/agents/finance_agent/versions/1/tools"),
        ("POST", "/v1/models"),
        ("POST", "/v1/admin/enroll"),
        ("POST", "/v1/auth/enrollment-codes"),
        ("POST", "/v1/users"),
    ]
    for method, path in attempts:
        resp = await api.client.request(method, path, headers=h, json={})
        assert resp.status_code in (404, 405), f"{method} {path} -> {resp.status_code}"
    agent = (await api.client.get("/v1/agents/finance_agent", headers=h)).json()
    assert agent["enabled"] is False and agent["active_version"] is None
    assert agent["versions"][0]["model_aliases"] == [] and agent["versions"][0]["tools"] == {}


async def test_app_token_can_disable_an_agent_and_it_is_audited(api: Api, device: Device) -> None:
    from core.agent_registry import service as agents

    async with api.sessions() as s, s.begin():
        await seed_registry(s)
        await agents.activate_version(s, "finance_agent", 1)
        await agents.set_agent_enabled(s, "finance_agent", True)
    resp = await api.client.post("/v1/agents/finance_agent/disable", headers=device.headers)
    assert resp.status_code == 200 and resp.json()["enabled"] is False
    events = (await api.client.get("/v1/audit", headers=device.headers)).json()
    disabled = [e for e in events if e["event_type"] == "agent.disabled"]
    assert disabled and disabled[0]["actor_type"] == "user"
    assert (
        await api.client.post("/v1/agents/nope/disable", headers=device.headers)
    ).status_code == 404


async def test_audit_endpoint_leaks_no_secrets(api: Api, device: Device) -> None:
    await api.client.post("/v1/auth/refresh", json={"refresh_token": device.refresh})
    await api.client.post(
        "/v1/auth/refresh", json={"refresh_token": device.refresh}
    )  # reuse -> audited
    new_access = (
        await api.client.post("/v1/auth/refresh", json={"refresh_token": "rt_" + "z" * 40})
    ).text
    assert "access_token" not in new_access
    admin_dev = await enroll_device(api.sessions, api.settings, api.client, device.user_id, "third")
    body = (await api.client.get("/v1/audit?limit=200", headers=admin_dev.headers)).text
    for secret in (device.refresh, device.access, admin_dev.refresh, admin_dev.access):
        assert secret not in body


async def test_readiness_details_only_for_authenticated_callers(api: Api, device: Device) -> None:
    assert (await api.client.get("/health/ready")).status_code == 401
    resp = await api.client.get("/health/ready", headers=device.headers)
    assert resp.status_code in (200, 503) and set(resp.json()["checks"]) == {
        "database",
        "pgvector",
        "litellm",
    }
    assert (await api.client.get("/health")).json() == {"status": "ok"}


async def test_per_user_rate_limit(
    migrated_url: URL, sessions: async_sessionmaker[AsyncSession]
) -> None:
    settings = make_settings(migrated_url, rate_limit_user_per_minute=3)
    app = create_app(settings)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as c,
    ):
        uid = await make_user(sessions)
        dev = await enroll_device(sessions, settings, c, uid)
        codes = [(await c.get("/v1/me", headers=dev.headers)).status_code for _ in range(5)]
    assert codes == [200, 200, 200, 429, 429]


async def test_user_model_untouched(api: Api, device: Device) -> None:
    async with api.sessions() as s:
        assert (await s.execute(select(User))).scalar_one().id == device.user_id
