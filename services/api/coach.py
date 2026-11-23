"""/v1/coach - the user's own goals, tasks, habits, calendar and reminders. The server never contacts anyone
and holds no calendar or notification credentials; the app polls /reminders/due."""

import datetime as dt
import uuid
from datetime import date, datetime, time
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from database.schemas import CoachEvent, CoachGoal, CoachHabit, CoachTask
from domain.coach.service import CoachError, CoachService
from services.api.auth.deps import PrincipalDep, SessionDep
from services.api.domain_deps import CipherDep, decode_b64, jsonable

router = APIRouter(prefix="/v1/coach", tags=["coach"])
Category = Literal["health", "career", "finance", "learning", "relationships", "personal"]


class Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class GoalIn(Body):
    title: str = Field(min_length=1, max_length=120)
    category: Category = "personal"
    target_date: date | None = None


class GoalPatch(Body):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    category: Category | None = None
    target_date: date | None = None
    progress_pct: int | None = Field(default=None, ge=0, le=100)
    status: Literal["active", "done", "paused", "dropped"] | None = None


class TaskIn(Body):
    title: str = Field(min_length=1, max_length=200)
    due_date: date | None = None
    priority: int = Field(default=3, ge=1, le=4)
    estimate_minutes: int | None = Field(default=None, ge=5, le=480)
    goal_id: uuid.UUID | None = None


class TaskPatch(Body):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    due_date: date | None = None
    priority: int | None = Field(default=None, ge=1, le=4)
    estimate_minutes: int | None = Field(default=None, ge=5, le=480)


class SnoozeIn(Body):
    until: date


class HabitIn(Body):
    title: str = Field(min_length=1, max_length=120)
    cadence: Literal["daily", "weekdays", "weekly"] = "daily"
    target_per_week: int = Field(default=7, ge=1, le=7)
    goal_id: uuid.UUID | None = None


class HabitLogIn(Body):
    date: dt.date | None = None  # (annotation spelled dt.date: the field name shadows `date` here)
    done: bool = True


class EventIn(Body):
    title: str = Field(min_length=1, max_length=200)
    start: datetime
    end: datetime
    all_day: bool = False
    reminder_minutes: int | None = Field(default=None, ge=0, le=10080)


class IcsIn(Body):
    content_base64: str = Field(min_length=4)


class ReminderIn(Body):
    text: str = Field(min_length=1, max_length=200)
    remind_at: datetime
    task_id: uuid.UUID | None = None


class CheckinIn(Body):
    kind: Literal["morning", "evening"]
    mood: int = Field(ge=1, le=5)
    energy: int = Field(ge=1, le=5)
    note: str | None = Field(default=None, max_length=500)


class SettingsIn(Body):
    timezone: str | None = Field(default=None, max_length=48)
    work_start: time | None = None
    work_end: time | None = None
    buffer_minutes: int | None = Field(default=None, ge=0, le=60)


async def _run(session: SessionDep, fn: Any) -> Any:
    try:
        res = await fn()
        await session.commit()
        return jsonable(res)
    except CoachError as exc:
        await session.rollback()
        return JSONResponse(
            status_code=exc.status, content={"error": {"code": exc.code, "message": exc.message}}
        )


def _svc(session: SessionDep, cipher: CipherDep) -> CoachService:
    return CoachService(session, cipher)


# ---- goals -------------------------------------------------------------------------------------------------------
@router.post("/goals", status_code=201)
async def add_goal(body: GoalIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.goal_view(
            await s.add_goal(p.user_id, **body.model_dump()), await s.today(p.user_id)
        )

    return await _run(session, go)


@router.get("/goals")
async def goals(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        t = await s.today(p.user_id)
        return [s.goal_view(g, t) for g in await s.goals(p.user_id)]

    return await _run(session, go)


@router.patch("/goals/{goal_id}")
async def patch_goal(
    goal_id: uuid.UUID, body: GoalPatch, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.goal_view(
            await s.update_goal(p.user_id, goal_id, **body.model_dump(exclude_unset=True)),
            await s.today(p.user_id),
        )

    return await _run(session, go)


# ---- tasks -------------------------------------------------------------------------------------------------------
@router.post("/tasks", status_code=201)
async def add_task(body: TaskIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.task_view(
            await s.add_task(
                p.user_id,
                body.title,
                due_date=body.due_date,
                priority=body.priority,
                estimate_minutes=body.estimate_minutes,
                goal_id=body.goal_id,
            )
        )

    return await _run(session, go)


@router.get("/tasks")
async def tasks(
    p: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    status: Literal["todo", "done", "snoozed", "dropped"] | None = None,
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return [s.task_view(t) for t in await s.tasks(p.user_id, status)]

    return await _run(session, go)


@router.patch("/tasks/{task_id}")
async def patch_task(
    task_id: uuid.UUID, body: TaskPatch, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.task_view(
            await s.update_task(p.user_id, task_id, **body.model_dump(exclude_unset=True))
        )

    return await _run(session, go)


@router.post("/tasks/{task_id}/complete")
async def complete_task(
    task_id: uuid.UUID, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.task_view(await s.complete_task(p.user_id, task_id))

    return await _run(session, go)


@router.post("/tasks/{task_id}/snooze")
async def snooze_task(
    task_id: uuid.UUID, body: SnoozeIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return s.task_view(await s.snooze_task(p.user_id, task_id, body.until))

    return await _run(session, go)


# ---- habits ------------------------------------------------------------------------------------------------------
@router.post("/habits", status_code=201)
async def add_habit(body: HabitIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        h = await s.add_habit(
            p.user_id, body.title, body.cadence, body.target_per_week, body.goal_id
        )
        return await s.habit_view(p.user_id, h.id)

    return await _run(session, go)


@router.get("/habits")
async def habits(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return [await s.habit_view(p.user_id, h.id) for h in await s.habits(p.user_id)]

    return await _run(session, go)


@router.post("/habits/{habit_id}/log")
async def log_habit(
    habit_id: uuid.UUID, body: HabitLogIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.log_habit(p.user_id, habit_id, body.date, body.done)

    return await _run(session, go)


# ---- calendar / reminders --------------------------------------------------------------------------------------------
@router.post("/events", status_code=201)
async def add_event(body: EventIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        for stamp in (body.start, body.end):
            if stamp.tzinfo is None:
                raise CoachError("timezone_required", "datetimes must include a UTC offset")
        e = await s.add_event(
            p.user_id,
            body.title,
            body.start,
            body.end,
            all_day=body.all_day,
            reminder_minutes=body.reminder_minutes,
        )
        return s.event_view(e, await s.tz(p.user_id))  # type: ignore[arg-type]

    return await _run(session, go)


@router.get("/events")
async def events(
    p: PrincipalDep, session: SessionDep, cipher: CipherDep, start: datetime, end: datetime
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        if start.tzinfo is None or end.tzinfo is None or (end - start).days > 92:
            raise CoachError("invalid_range", "timezone-aware range of at most 92 days required")
        tz = await s.tz(p.user_id)
        return [s.event_view(e, tz) for e in await s.events(p.user_id, start, end)]

    return await _run(session, go)


@router.post("/calendar/import")
async def import_calendar(
    body: IcsIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    """User-supplied .ics (an export, or the output of an iOS Shortcut). Re-importing never duplicates."""
    data = decode_b64(body.content_base64, 450_000)
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.import_ics(p.user_id, data)

    return await _run(session, go)


@router.post("/reminders", status_code=201)
async def add_reminder(
    body: ReminderIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        if body.remind_at.tzinfo is None:
            raise CoachError("timezone_required", "remind_at must include a UTC offset")
        r = await s.add_reminder(p.user_id, body.text, body.remind_at, body.task_id)
        return {"id": r.id, "remind_at": r.remind_at, "status": r.status}

    return await _run(session, go)


@router.get("/reminders/due")
async def due_reminders(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.due_reminders(p.user_id)

    return await _run(session, go)


@router.post("/reminders/{reminder_id}/{action}")
async def ack_reminder(
    reminder_id: uuid.UUID,
    action: Literal["ack", "dismiss"],
    p: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        await s.ack_reminder(p.user_id, reminder_id, dismiss=action == "dismiss")
        return {"id": reminder_id, "status": "dismissed" if action == "dismiss" else "delivered"}

    return await _run(session, go)


# ---- check-ins, settings, plan, briefing, review, progress ----------------------------------------------------------------
@router.post("/checkins", status_code=201)
async def checkin(body: CheckinIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.checkin(p.user_id, body.kind, body.mood, body.energy, body.note)

    return await _run(session, go)


@router.put("/settings")
async def put_settings(
    body: SettingsIn, p: PrincipalDep, session: SessionDep, cipher: CipherDep
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        r = await s.update_settings(p.user_id, **body.model_dump(exclude_unset=True))
        return {
            "timezone": r.timezone,
            "work_start": r.work_start,
            "work_end": r.work_end,
            "buffer_minutes": r.buffer_minutes,
        }

    return await _run(session, go)


@router.get("/settings")
async def get_settings(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        r = await s.settings(p.user_id)
        return {
            "timezone": r.timezone,
            "work_start": r.work_start,
            "work_end": r.work_end,
            "buffer_minutes": r.buffer_minutes,
        }

    return await _run(session, go)


@router.get("/plan")
async def plan(
    p: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    day: date | None = None,
    max_tasks: Annotated[int, Query(ge=1, le=20)] = 8,
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.plan(p.user_id, day, max_tasks)

    return await _run(session, go)


@router.get("/briefing")
async def briefing(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.briefing(p.user_id)

    return await _run(session, go)


@router.get("/review")
async def review(p: PrincipalDep, session: SessionDep, cipher: CipherDep) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.review(p.user_id)

    return await _run(session, go)


@router.get("/progress")
async def progress(
    p: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
    days: Annotated[int, Query(ge=1, le=90)] = 7,
) -> Any:
    s = _svc(session, cipher)

    async def go() -> Any:
        return await s.progress(p.user_id, days)

    return await _run(session, go)


@router.delete("/{kind}/{rid}", status_code=204, response_class=Response, response_model=None)
async def delete_item(
    kind: Literal["goals", "tasks", "habits", "events"],
    rid: uuid.UUID,
    p: PrincipalDep,
    session: SessionDep,
    cipher: CipherDep,
) -> Any:
    s = _svc(session, cipher)
    model = {"goals": CoachGoal, "tasks": CoachTask, "habits": CoachHabit, "events": CoachEvent}[
        kind
    ]

    async def go() -> None:
        await s.delete(model, p.user_id, rid)

    res = await _run(session, go)
    return res if isinstance(res, JSONResponse) else Response(status_code=204)
