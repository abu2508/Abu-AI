# mypy: disable-error-code="no-untyped-call,no-untyped-def,arg-type,no-any-return,index"
"""Coach API + service against a real database. Synthetic data only."""

import base64
import json
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import text

from tests.integration.conftest import Api
from tests.integration.helpers import Device, enroll_device, make_user
from tests.unit.test_coach_logic import ICS
from tests.unit.test_http_hardening import _routes

DAY = date.today() + timedelta(days=2)  # a future day: the plan then uses the whole working window


async def dev2(api: Api, name: str = "B") -> Device:
    return await enroll_device(
        api.sessions, api.settings, api.client, await make_user(api.sessions, name)
    )


async def post(api, dev, path, body=None, expect=200):
    r = await api.client.post(
        f"/v1/coach{path}", json=body if body is not None else {}, headers=dev.headers
    )
    assert r.status_code == expect, r.text
    return r.json()


async def get(api, dev, path, expect=200, **params):
    r = await api.client.get(f"/v1/coach{path}", params=params, headers=dev.headers)
    assert r.status_code == expect, r.text
    return r.json()


async def test_every_coach_route_needs_authentication(api: Api) -> None:
    routes = [(m, p) for m, p in _routes(api.app) if p.startswith("/v1/coach")]
    assert len(routes) >= 20
    for method, path in routes:
        assert (await api.client.request(method, path, json={})).status_code == 401, (method, path)


async def test_users_are_isolated(api: Api, device: Device) -> None:
    b = await dev2(api)
    g = await post(api, device, "/goals", {"title": "Run a 10k", "category": "health"}, 201)
    t = await post(api, device, "/tasks", {"title": "Buy shoes", "goal_id": g["id"]}, 201)
    h = await post(api, device, "/habits", {"title": "Stretch"}, 201)
    assert (
        await get(api, b, "/goals") == []
        and await get(api, b, "/tasks") == []
        and await get(api, b, "/habits") == []
    )
    assert (
        await api.client.post(f"/v1/coach/tasks/{t['id']}/complete", json={}, headers=b.headers)
    ).status_code == 404
    assert (
        await api.client.post(f"/v1/coach/habits/{h['id']}/log", json={}, headers=b.headers)
    ).status_code == 404
    assert (
        await api.client.patch(
            f"/v1/coach/goals/{g['id']}", json={"progress_pct": 100}, headers=b.headers
        )
    ).status_code == 404
    assert (
        await api.client.delete(f"/v1/coach/tasks/{t['id']}", headers=b.headers)
    ).status_code == 404
    assert (
        await api.client.post(
            "/v1/coach/tasks", json={"title": "x", "goal_id": g["id"]}, headers=b.headers
        )
    ).status_code == 404
    assert (await get(api, device, "/tasks"))[0]["status"] == "todo"


async def test_strict_validation(api: Api, device: Device) -> None:
    bad_bodies = (
        ("/tasks", {"title": "x", "priority": 9}),
        ("/tasks", {"title": "x", "estimate_minutes": 1}),
        ("/tasks", {"title": ""}),
        ("/goals", {"title": "x", "category": "fun"}),
        ("/goals", {"title": "x", "owner": "y"}),
        ("/habits", {"title": "x", "cadence": "hourly"}),
        ("/events", {"title": "x", "start": "2024-01-01T10:00:00", "end": "2024-01-01T11:00:00"}),
        ("/reminders", {"text": "x", "remind_at": "2024-01-01T10:00:00"}),
        ("/checkins", {"kind": "morning", "mood": 9, "energy": 3}),
    )
    for path, body in bad_bodies:
        r = await api.client.post(f"/v1/coach{path}", json=body, headers=device.headers)
        assert r.status_code == 422, (path, body)
    assert (
        await api.client.put(
            "/v1/coach/settings", json={"timezone": "Mars/Base"}, headers=device.headers
        )
    ).status_code == 422
    assert (
        await api.client.put(
            "/v1/coach/settings",
            json={"work_start": "18:00", "work_end": "09:00"},
            headers=device.headers,
        )
    ).status_code == 422


async def test_plan_around_calendar_events(api: Api, device: Device) -> None:
    await api.client.put(
        "/v1/coach/settings",
        json={"timezone": "UTC", "work_start": "09:00", "work_end": "12:00", "buffer_minutes": 0},
        headers=device.headers,
    )
    start = datetime.combine(DAY, datetime.min.time(), tzinfo=UTC).replace(hour=10)
    await post(
        api,
        device,
        "/events",
        {
            "title": "Standup",
            "start": start.isoformat(),
            "end": (start + timedelta(minutes=30)).isoformat(),
        },
        201,
    )
    await post(
        api,
        device,
        "/tasks",
        {
            "title": "Write report",
            "priority": 1,
            "due_date": DAY.isoformat(),
            "estimate_minutes": 90,
        },
        201,
    )
    await post(
        api, device, "/tasks", {"title": "Email Asha", "priority": 3, "estimate_minutes": 20}, 201
    )
    await post(
        api, device, "/tasks", {"title": "Huge thing", "priority": 4, "estimate_minutes": 400}, 201
    )
    p = await get(api, device, "/plan", day=DAY.isoformat())
    by = {b["title"]: b["start"][11:16] for b in p["blocks"]}
    assert by == {
        "Write report": "10:30",
        "Email Asha": "09:00",
    }  # the 90-minute task only fits after the standup
    assert [u["title"] for u in p["unscheduled"]] == ["Huge thing"] and p["unscheduled"][0][
        "reason"
    ] == "no_free_window"
    assert p["free_minutes"] == 180 - 30 and p["planned_minutes"] == 110 and len(p["events"]) == 1


async def test_ics_import_is_idempotent_and_safe(api: Api, device: Device) -> None:
    b64 = base64.b64encode(ICS).decode()
    first = await post(api, device, "/calendar/import", {"content_base64": b64})
    assert first == {"imported": 3, "duplicates": 0, "skipped_recurring": 1, "skipped_invalid": 1}
    again = await post(api, device, "/calendar/import", {"content_base64": b64})
    assert again["imported"] == 0 and again["duplicates"] == 3
    evs = await get(
        api, device, "/events", start="2024-03-01T00:00:00+00:00", end="2024-03-30T00:00:00+00:00"
    )
    assert len(evs) == 3 and evs[0]["title"] == "Team sync, weekly"
    for bad in ("###", base64.b64encode(b"hello").decode()):
        assert (
            await api.client.post(
                "/v1/coach/calendar/import", json={"content_base64": bad}, headers=device.headers
            )
        ).status_code == 422
    huge = base64.b64encode(b"x" * 700_000).decode()
    assert (
        await api.client.post(
            "/v1/coach/calendar/import", json={"content_base64": huge}, headers=device.headers
        )
    ).status_code == 413


async def test_reminders_polling_and_event_reminders(api: Api, device: Device) -> None:
    past = (datetime.now(UTC) - timedelta(minutes=5)).isoformat()
    soon = datetime.now(UTC) + timedelta(minutes=30)
    r = await post(api, device, "/reminders", {"text": "Call the bank", "remind_at": past}, 201)
    await post(
        api,
        device,
        "/reminders",
        {"text": "later", "remind_at": (datetime.now(UTC) + timedelta(days=1)).isoformat()},
        201,
    )
    await post(
        api,
        device,
        "/events",
        {
            "title": "Doctor",
            "start": soon.isoformat(),
            "end": (soon + timedelta(hours=1)).isoformat(),
            "reminder_minutes": 60,
        },
        201,
    )
    due = await get(api, device, "/reminders/due")
    assert {d["text"] for d in due} == {
        "Call the bank",
        "Doctor",
    }  # the event reminder (start - 60 min) is already due
    await post(api, device, f"/reminders/{r['id']}/ack")
    assert {d["text"] for d in await get(api, device, "/reminders/due")} == {"Doctor"}
    other = await dev2(api)
    assert (
        await api.client.post(
            f"/v1/coach/reminders/{r['id']}/dismiss", json={}, headers=other.headers
        )
    ).status_code == 404


async def test_habits_streaks_and_logging_rules(api: Api, device: Device) -> None:
    h = await post(api, device, "/habits", {"title": "Read", "cadence": "daily"}, 201)
    today = (await get(api, device, "/briefing"))["date"]
    today = date.fromisoformat(today)
    for back in (0, 1, 2):
        out = await post(
            api,
            device,
            f"/habits/{h['id']}/log",
            {"date": (today - timedelta(days=back)).isoformat()},
        )
    assert out["current_streak"] == 3 and out["done_today"] is True
    again = await post(api, device, f"/habits/{h['id']}/log", {"date": today.isoformat()})
    assert again["current_streak"] == 3
    undone = await post(
        api, device, f"/habits/{h['id']}/log", {"date": today.isoformat(), "done": False}
    )
    assert (
        undone["current_streak"] == 2 and undone["done_today"] is False
    )  # today still open: yesterday's streak stands
    for bad in ((today + timedelta(days=1)).isoformat(), (today - timedelta(days=61)).isoformat()):
        assert (
            await api.client.post(
                f"/v1/coach/habits/{h['id']}/log", json={"date": bad}, headers=device.headers
            )
        ).status_code == 422


async def test_goals_tasks_and_snooze(api: Api, device: Device) -> None:
    today = date.fromisoformat((await get(api, device, "/briefing"))["date"])
    g = await post(
        api,
        device,
        "/goals",
        {
            "title": "Learn SQL",
            "category": "learning",
            "target_date": (today + timedelta(days=60)).isoformat(),
        },
        201,
    )
    assert g["progress_state"] == "on_track" and g["progress_pct"] == 0
    done = (
        await api.client.patch(
            f"/v1/coach/goals/{g['id']}", json={"progress_pct": 100}, headers=device.headers
        )
    ).json()
    assert done["status"] == "done"
    t = await post(api, device, "/tasks", {"title": "Chapter 1"}, 201)
    sn = await post(
        api, device, f"/tasks/{t['id']}/snooze", {"until": (today + timedelta(days=3)).isoformat()}
    )
    assert sn["status"] == "snoozed"
    assert (
        await api.client.post(
            f"/v1/coach/tasks/{t['id']}/snooze",
            json={"until": today.isoformat()},
            headers=device.headers,
        )
    ).status_code == 422
    p = await get(
        api, device, "/plan", day=(today + timedelta(days=2)).isoformat()
    )  # snooze has not ended yet: not planned
    assert p["blocks"] == []
    p5 = await get(
        api, device, "/plan", day=(today + timedelta(days=4)).isoformat()
    )  # snooze ended: back on the plan
    assert [b["title"] for b in p5["blocks"]] == ["Chapter 1"]
    assert (await post(api, device, f"/tasks/{t['id']}/complete"))["status"] == "done"


async def test_briefing_review_progress_and_checkins(api: Api, device: Device) -> None:
    today = date.fromisoformat((await get(api, device, "/briefing"))["date"])
    await post(
        api,
        device,
        "/tasks",
        {"title": "Pay attention", "priority": 1, "due_date": today.isoformat()},
        201,
    )
    h = await post(api, device, "/habits", {"title": "Meditate"}, 201)
    b = await get(api, device, "/briefing")
    assert (
        [x["title"] for x in b["habits_open"]] == ["Meditate"]
        and "plan" in b
        and b["mood_trend"]["checkins"] == 0
    )
    await post(api, device, f"/habits/{h['id']}/log")
    assert (await get(api, device, "/briefing"))["habits_open"] == []
    assert (
        await post(
            api,
            device,
            "/checkins",
            {"kind": "morning", "mood": 4, "energy": 3, "note": "slept well"},
            201,
        )
    )["mood"] == 4
    dup = await api.client.post(
        "/v1/coach/checkins",
        json={"kind": "morning", "mood": 2, "energy": 2},
        headers=device.headers,
    )
    assert dup.status_code == 409 and dup.json()["error"]["code"] == "already_checked_in"
    r = await get(api, device, "/review")
    assert (
        r["habits_done"] == 1
        and r["habits_due"] == 1
        and r["tasks_completed_today"] == 0
        and r["mood_trend"]["avg_mood"] == 4.0
    )
    pr = await get(api, device, "/progress", days=7)
    assert pr["tasks_open"] == 1 and pr["habits"][0]["current_streak"] == 1


async def test_titles_and_notes_are_encrypted_at_rest(api: Api, device: Device) -> None:
    await post(api, device, "/goals", {"title": "Secret goal title"}, 201)
    await post(api, device, "/tasks", {"title": "Secret task title"}, 201)
    await post(api, device, "/habits", {"title": "Secret habit title"}, 201)
    await post(
        api,
        device,
        "/checkins",
        {"kind": "evening", "mood": 3, "energy": 3, "note": "Secret note text"},
        201,
    )
    await post(
        api,
        device,
        "/events",
        {
            "title": "Secret event title",
            "start": "2030-01-01T10:00:00+00:00",
            "end": "2030-01-01T11:00:00+00:00",
        },
        201,
    )
    async with api.sessions() as s:
        dump = ""
        for t in (
            "coach_goals",
            "coach_tasks",
            "coach_habits",
            "coach_checkins",
            "coach_events",
            "coach_reminders",
        ):
            dump += json.dumps(
                [
                    [str(c) for c in row]
                    for row in (await s.execute(text(f"SELECT * FROM {t}"))).all()
                ]
            )
        assert "Secret" not in dump


async def test_deleting_items_and_data_survives_the_kill_switch(api: Api, device: Device) -> None:
    from core.audit.service import ADMIN
    from core.governance import killswitch

    t = await post(api, device, "/tasks", {"title": "x"}, 201)
    await killswitch.activate(api.sessions, ADMIN, "drill")
    assert (
        await api.client.get("/v1/coach/plan", headers=device.headers)
    ).status_code == 200  # the owner's own data stays usable
    assert (
        await api.client.delete(f"/v1/coach/tasks/{t['id']}", headers=device.headers)
    ).status_code == 204
    assert await get(api, device, "/tasks") == []


async def test_erase_removes_every_domain_record(api: Api, device: Device) -> None:
    """The existing 'erase everything' endpoint must also clear finance, messaging and coach data."""
    from tests.integration.test_finance import bank, txn
    from tests.integration.test_messaging import J

    acct = await bank(api, device)
    await api.client.post(
        "/v1/finance/transactions",
        json=txn(acct, 3, "10.00", "debit", "tea"),
        headers=device.headers,
    )
    await api.client.post(
        "/v1/messaging/conversations/import",
        json={"platform": "sms", "format": "json", "text": J},
        headers=device.headers,
    )
    await post(api, device, "/goals", {"title": "g"}, 201)
    await post(api, device, "/habits", {"title": "h"}, 201)
    await post(api, device, "/checkins", {"kind": "morning", "mood": 3, "energy": 3}, 201)
    r = await api.client.post(
        "/v1/memory/erase", json={"confirm": "ERASE ALL MY MEMORY"}, headers=device.headers
    )
    assert r.status_code == 200, r.text
    async with api.sessions() as s:
        for t in (
            "fin_accounts",
            "fin_transactions",
            "msg_conversations",
            "msg_messages",
            "coach_goals",
            "coach_habits",
            "coach_checkins",
            "coach_settings",
        ):
            assert (await s.execute(text(f"SELECT count(*) FROM {t}"))).scalar_one() == 0, t
