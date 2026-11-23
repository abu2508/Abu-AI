"""Coach service: goals, tasks, habits, calendar events, reminders, check-ins, and the deterministic daily
plan / briefing / review built from them. User-scoped on every call; no external calendar or notification
access is assumed. Nothing here contacts anyone."""

import uuid
from datetime import UTC, date, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from database.schemas import (
    CoachCheckin,
    CoachEvent,
    CoachGoal,
    CoachHabit,
    CoachHabitLog,
    CoachReminder,
    CoachSettings,
    CoachTask,
)
from domain.coach import ics, planning
from domain.common.crypto import FieldCipher


class CoachError(Exception):
    def __init__(self, code: str, message: str, status: int = 422) -> None:
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


class CoachService:
    def __init__(
        self, session: AsyncSession, cipher: FieldCipher, now: datetime | None = None
    ) -> None:
        self._s, self._c, self._now = session, cipher, now

    def now(self) -> datetime:
        return self._now or datetime.now(UTC)

    # ----------------------------------------------------------------- settings / time
    async def settings(self, user_id: uuid.UUID) -> CoachSettings:
        row = await self._s.get(CoachSettings, user_id)
        if row is None:
            row = CoachSettings(
                user_id=user_id,
                timezone="Asia/Kolkata",
                work_start=time(9, 0),
                work_end=time(18, 0),
                buffer_minutes=10,
            )
            self._s.add(row)
            await self._s.flush()
        return row

    async def update_settings(self, user_id: uuid.UUID, **f: Any) -> CoachSettings:
        row = await self.settings(user_id)
        if f.get("timezone") is not None:
            try:
                ZoneInfo(f["timezone"])
            except (ZoneInfoNotFoundError, ValueError, OSError):
                raise CoachError("invalid_timezone", "unknown timezone") from None
            row.timezone = f["timezone"]
        for k in ("work_start", "work_end", "buffer_minutes"):
            if f.get(k) is not None:
                setattr(row, k, f[k])
        if row.work_start >= row.work_end:
            raise CoachError("invalid_hours", "work_start must be before work_end")
        return row

    async def tz(self, user_id: uuid.UUID) -> ZoneInfo:
        return ZoneInfo((await self.settings(user_id)).timezone)

    async def today(self, user_id: uuid.UUID) -> date:
        return self.now().astimezone(await self.tz(user_id)).date()

    # ----------------------------------------------------------------- goals
    async def _own(self, model: Any, user_id: uuid.UUID, rid: uuid.UUID) -> Any:
        row = (
            await self._s.execute(select(model).where(model.id == rid, model.user_id == user_id))
        ).scalar_one_or_none()
        if row is None:
            raise CoachError("not_found", "not found", 404)
        return row

    async def add_goal(
        self,
        user_id: uuid.UUID,
        title: str,
        category: str = "personal",
        target_date: date | None = None,
    ) -> CoachGoal:
        rid = uuid.uuid4()
        g = CoachGoal(
            id=rid,
            user_id=user_id,
            title_enc=self._c.seal(user_id, rid, "coach.goal.title", title[:120]),
            category=category,
            target_date=target_date,
        )
        self._s.add(g)
        await self._s.flush()
        return g

    async def update_goal(self, user_id: uuid.UUID, goal_id: uuid.UUID, **f: Any) -> CoachGoal:
        g: CoachGoal = await self._own(CoachGoal, user_id, goal_id)
        for k in ("progress_pct", "status", "target_date", "category"):
            if k in f and f[k] is not None:
                setattr(g, k, f[k])
        if f.get("title"):
            g.title_enc = self._c.seal(user_id, g.id, "coach.goal.title", f["title"][:120])
        if g.status == "done" or g.progress_pct == 100:
            g.status, g.progress_pct, g.completed_at = "done", 100, g.completed_at or self.now()
        return g

    async def goals(self, user_id: uuid.UUID, status: str | None = None) -> list[CoachGoal]:
        q = select(CoachGoal).where(CoachGoal.user_id == user_id).order_by(CoachGoal.created_at)
        return list(
            (await self._s.execute(q.where(CoachGoal.status == status) if status else q)).scalars()
        )

    def goal_view(self, g: CoachGoal, today: date) -> dict[str, Any]:
        return {
            "id": g.id,
            "title": self._c.open(g.user_id, g.id, "coach.goal.title", g.title_enc),
            "category": g.category,
            "status": g.status,
            "progress_pct": g.progress_pct,
            "target_date": g.target_date,
            **{
                f"progress_{k}": v
                for k, v in planning.goal_status(
                    g.progress_pct, g.created_at.date(), g.target_date, today, g.status
                ).items()
            },
        }

    # ----------------------------------------------------------------- tasks
    async def add_task(
        self,
        user_id: uuid.UUID,
        title: str,
        *,
        due_date: date | None = None,
        priority: int = 3,
        estimate_minutes: int | None = None,
        goal_id: uuid.UUID | None = None,
    ) -> CoachTask:
        if goal_id:
            await self._own(CoachGoal, user_id, goal_id)
        rid = uuid.uuid4()
        t = CoachTask(
            id=rid,
            user_id=user_id,
            title_enc=self._c.seal(user_id, rid, "coach.task.title", title[:200]),
            due_date=due_date,
            priority=priority,
            estimate_minutes=estimate_minutes,
            goal_id=goal_id,
        )
        self._s.add(t)
        await self._s.flush()
        return t

    async def tasks(self, user_id: uuid.UUID, status: str | None = None) -> list[CoachTask]:
        q = select(CoachTask).where(CoachTask.user_id == user_id).order_by(CoachTask.created_at)
        return list(
            (await self._s.execute(q.where(CoachTask.status == status) if status else q)).scalars()
        )

    def task_view(self, t: CoachTask) -> dict[str, Any]:
        return {
            "id": t.id,
            "title": self._c.open(t.user_id, t.id, "coach.task.title", t.title_enc),
            "due_date": t.due_date,
            "priority": t.priority,
            "estimate_minutes": t.estimate_minutes,
            "status": t.status,
            "snoozed_until": t.snoozed_until,
            "goal_id": t.goal_id,
        }

    async def complete_task(self, user_id: uuid.UUID, task_id: uuid.UUID) -> CoachTask:
        t: CoachTask = await self._own(CoachTask, user_id, task_id)
        t.status, t.completed_at = "done", self.now()
        return t

    async def snooze_task(self, user_id: uuid.UUID, task_id: uuid.UUID, until: date) -> CoachTask:
        t: CoachTask = await self._own(CoachTask, user_id, task_id)
        if until <= await self.today(user_id):
            raise CoachError("invalid_date", "snooze until a future date")
        t.status, t.snoozed_until = "snoozed", until
        return t

    async def update_task(self, user_id: uuid.UUID, task_id: uuid.UUID, **f: Any) -> CoachTask:
        t: CoachTask = await self._own(CoachTask, user_id, task_id)
        for k in ("due_date", "priority", "estimate_minutes", "status"):
            if k in f and f[k] is not None:
                setattr(t, k, f[k])
        if f.get("title"):
            t.title_enc = self._c.seal(user_id, t.id, "coach.task.title", f["title"][:200])
        return t

    async def delete(self, model: Any, user_id: uuid.UUID, rid: uuid.UUID) -> None:
        await self._own(model, user_id, rid)
        await self._s.execute(delete(model).where(model.id == rid, model.user_id == user_id))

    async def _open_tasks(self, user_id: uuid.UUID, today: date) -> list[planning.TaskView]:
        """Todo tasks, plus snoozed tasks whose snooze has ended (they come back on their own)."""
        goals = {g.id: g for g in await self.goals(user_id, "active")}
        out = []
        for i, t in enumerate(await self.tasks(user_id)):
            if t.status == "todo" or (
                t.status == "snoozed" and t.snoozed_until is not None and t.snoozed_until <= today
            ):
                g = goals.get(t.goal_id) if t.goal_id else None
                out.append(
                    planning.TaskView(
                        str(t.id),
                        self._c.open(user_id, t.id, "coach.task.title", t.title_enc),
                        t.priority,
                        t.due_date,
                        t.estimate_minutes,
                        g is not None,
                        (g.target_date - today).days if g and g.target_date else None,
                        i,
                    )
                )
        return out

    # ----------------------------------------------------------------- habits
    async def add_habit(
        self,
        user_id: uuid.UUID,
        title: str,
        cadence: str = "daily",
        target_per_week: int = 7,
        goal_id: uuid.UUID | None = None,
    ) -> CoachHabit:
        if goal_id:
            await self._own(CoachGoal, user_id, goal_id)
        rid = uuid.uuid4()
        h = CoachHabit(
            id=rid,
            user_id=user_id,
            title_enc=self._c.seal(user_id, rid, "coach.habit.title", title[:120]),
            cadence=cadence,
            target_per_week=target_per_week if cadence == "weekly" else 7,
            goal_id=goal_id,
        )
        self._s.add(h)
        await self._s.flush()
        return h

    async def habits(self, user_id: uuid.UUID, active_only: bool = True) -> list[CoachHabit]:
        q = select(CoachHabit).where(CoachHabit.user_id == user_id).order_by(CoachHabit.created_at)
        return list(
            (
                await self._s.execute(q.where(CoachHabit.active.is_(True)) if active_only else q)
            ).scalars()
        )

    async def log_habit(
        self, user_id: uuid.UUID, habit_id: uuid.UUID, day: date | None = None, done: bool = True
    ) -> dict[str, Any]:
        await self._own(CoachHabit, user_id, habit_id)
        today = await self.today(user_id)
        day = day or today
        if day > today or (today - day).days > 60:
            raise CoachError(
                "invalid_date", "a habit can be logged for today or up to 60 days back"
            )
        if done:
            await self._s.execute(
                insert(CoachHabitLog)
                .values(user_id=user_id, habit_id=habit_id, log_date=day)
                .on_conflict_do_nothing()
            )
        else:
            await self._s.execute(
                delete(CoachHabitLog).where(
                    CoachHabitLog.habit_id == habit_id,
                    CoachHabitLog.user_id == user_id,
                    CoachHabitLog.log_date == day,
                )
            )
        return await self.habit_view(user_id, habit_id)

    async def habit_view(self, user_id: uuid.UUID, habit_id: uuid.UUID) -> dict[str, Any]:
        h: CoachHabit = await self._own(CoachHabit, user_id, habit_id)
        today = await self.today(user_id)
        logs = set(
            (
                await self._s.execute(
                    select(CoachHabitLog.log_date).where(
                        CoachHabitLog.habit_id == habit_id, CoachHabitLog.user_id == user_id
                    )
                )
            ).scalars()
        )
        return {
            "id": h.id,
            "title": self._c.open(user_id, h.id, "coach.habit.title", h.title_enc),
            "cadence": h.cadence,
            "target_per_week": h.target_per_week,
            **planning.habit_stats(h.cadence, h.target_per_week, logs, today),
        }

    # ----------------------------------------------------------------- events / reminders
    async def add_event(
        self,
        user_id: uuid.UUID,
        title: str,
        start: datetime,
        end: datetime,
        *,
        all_day: bool = False,
        source: str = "manual",
        external_hash: str | None = None,
        reminder_minutes: int | None = None,
    ) -> CoachEvent | None:
        if end < start:
            raise CoachError("invalid_range", "end must not be before start")
        rid = uuid.uuid4()
        res = await self._s.execute(
            insert(CoachEvent)
            .values(
                id=rid,
                user_id=user_id,
                title_enc=self._c.seal(user_id, rid, "coach.event.title", title[:200]),
                start_at=start,
                end_at=end,
                all_day=all_day,
                source=source,
                external_hash=external_hash,
                reminder_minutes=reminder_minutes,
            )
            .on_conflict_do_nothing()
            .returning(CoachEvent.id)
        )
        if res.scalar_one_or_none() is None:
            return None  # same external event already present
        if reminder_minutes is not None:
            reminder_id = uuid.uuid4()  # the ciphertext is bound to the REMINDER's own id
            self._s.add(
                CoachReminder(
                    id=reminder_id,
                    user_id=user_id,
                    text_enc=self._c.seal(user_id, reminder_id, "coach.reminder.text", title[:200]),
                    event_id=rid,
                    remind_at=start - timedelta(minutes=reminder_minutes),
                )
            )
        await self._s.flush()
        return await self._s.get(CoachEvent, rid)

    async def import_ics(self, user_id: uuid.UUID, data: bytes) -> dict[str, Any]:
        try:
            parsed = ics.parse_ics(data, await self.tz(user_id))
        except ics.IcsError as exc:
            raise CoachError("import_failed", f"could not read the calendar file ({exc})") from None
        added = dup = 0
        for ev in parsed.events:
            eh = self._c.hash(user_id, "coach.ics", ev.uid, ev.start.isoformat())
            if await self.add_event(
                user_id,
                ev.title,
                ev.start,
                ev.end,
                all_day=ev.all_day,
                source="ics",
                external_hash=eh,
            ):
                added += 1
            else:
                dup += 1
        return {
            "imported": added,
            "duplicates": dup,
            "skipped_recurring": parsed.skipped_recurring,
            "skipped_invalid": parsed.skipped_invalid,
        }

    async def events(self, user_id: uuid.UUID, start: datetime, end: datetime) -> list[CoachEvent]:
        return list(
            (
                await self._s.execute(
                    select(CoachEvent)
                    .where(
                        CoachEvent.user_id == user_id,
                        CoachEvent.end_at > start,
                        CoachEvent.start_at < end,
                    )
                    .order_by(CoachEvent.start_at)
                )
            ).scalars()
        )

    def event_view(self, e: CoachEvent, tz: ZoneInfo) -> dict[str, Any]:
        return {
            "id": e.id,
            "title": self._c.open(e.user_id, e.id, "coach.event.title", e.title_enc),
            "start": e.start_at.astimezone(tz),
            "end": e.end_at.astimezone(tz),
            "all_day": e.all_day,
            "source": e.source,
            "reminder_minutes": e.reminder_minutes,
        }

    async def add_reminder(
        self, user_id: uuid.UUID, text: str, remind_at: datetime, task_id: uuid.UUID | None = None
    ) -> CoachReminder:
        if task_id:
            await self._own(CoachTask, user_id, task_id)
        rid = uuid.uuid4()
        r = CoachReminder(
            id=rid,
            user_id=user_id,
            text_enc=self._c.seal(user_id, rid, "coach.reminder.text", text[:200]),
            remind_at=remind_at,
            task_id=task_id,
        )
        self._s.add(r)
        await self._s.flush()
        return r

    async def due_reminders(self, user_id: uuid.UUID, limit: int = 50) -> list[dict[str, Any]]:
        rows = (
            await self._s.execute(
                select(CoachReminder)
                .where(
                    CoachReminder.user_id == user_id,
                    CoachReminder.status == "pending",
                    CoachReminder.remind_at <= self.now(),
                )
                .order_by(CoachReminder.remind_at)
                .limit(limit)
            )
        ).scalars()
        return [
            {
                "id": r.id,
                "text": self._c.open(user_id, r.id, "coach.reminder.text", r.text_enc),
                "remind_at": r.remind_at,
                "event_id": r.event_id,
                "task_id": r.task_id,
            }
            for r in rows
        ]

    async def ack_reminder(
        self, user_id: uuid.UUID, reminder_id: uuid.UUID, dismiss: bool = False
    ) -> None:
        r: CoachReminder = await self._own(CoachReminder, user_id, reminder_id)
        r.status, r.delivered_at = "dismissed" if dismiss else "delivered", self.now()

    # ----------------------------------------------------------------- check-ins
    async def checkin(
        self, user_id: uuid.UUID, kind: str, mood: int, energy: int, note: str | None = None
    ) -> dict[str, Any]:
        day = await self.today(user_id)
        rid = uuid.uuid4()
        enc = self._c.seal(user_id, rid, "coach.checkin.note", note[:500]) if note else None
        res = await self._s.execute(
            insert(CoachCheckin)
            .values(
                id=rid,
                user_id=user_id,
                checkin_date=day,
                kind=kind,
                mood=mood,
                energy=energy,
                note_enc=enc,
            )
            .on_conflict_do_nothing()
            .returning(CoachCheckin.id)
        )
        if res.scalar_one_or_none() is None:
            raise CoachError(
                "already_checked_in", f"you already did the {kind} check-in today", 409
            )
        return {"date": day, "kind": kind, "mood": mood, "energy": energy}

    async def mood_trend(self, user_id: uuid.UUID, today: date, days: int = 7) -> dict[str, Any]:
        rows = (
            await self._s.execute(
                select(CoachCheckin.mood, CoachCheckin.energy).where(
                    CoachCheckin.user_id == user_id,
                    CoachCheckin.checkin_date > today - timedelta(days=days),
                )
            )
        ).all()
        if not rows:
            return {"checkins": 0, "avg_mood": None, "avg_energy": None}
        return {
            "checkins": len(rows),
            "avg_mood": round(sum(r[0] for r in rows) / len(rows), 1),
            "avg_energy": round(sum(r[1] for r in rows) / len(rows), 1),
        }

    # ----------------------------------------------------------------- plan / briefing / review / progress
    async def _day_bounds(
        self, user_id: uuid.UUID, day: date
    ) -> tuple[datetime, datetime, datetime, datetime, ZoneInfo]:
        tz, st = await self.tz(user_id), await self.settings(user_id)
        return (
            planning.combine(day, time(0), tz),
            planning.combine(day + timedelta(days=1), time(0), tz),
            planning.combine(day, st.work_start, tz),
            planning.combine(day, st.work_end, tz),
            tz,
        )

    async def plan(
        self, user_id: uuid.UUID, day: date | None = None, max_tasks: int = 8
    ) -> dict[str, Any]:
        today = await self.today(user_id)
        day = day or today
        d0, d1, w0, w1, tz = await self._day_bounds(user_id, day)
        evs = await self.events(user_id, d0, d1)
        st = await self.settings(user_id)
        busy = [(e.start_at, e.end_at) for e in evs if not e.all_day]
        windows = planning.free_windows(
            max(w0, self.now()) if day == today else w0, w1, busy, st.buffer_minutes
        )
        tasks = await self._open_tasks(user_id, day)
        ranked = planning.rank_tasks(tasks, day)
        plan = planning.build_plan(ranked, windows, max_tasks)
        names = {t.id: t.title for t in tasks}
        for b in plan["blocks"]:  # type: ignore[attr-defined]
            b["title"], b["start"], b["end"] = (
                names[b["task_id"]],
                b["start"].astimezone(tz),
                b["end"].astimezone(tz),
            )
        for u in [*plan["unscheduled"], *plan["deferred"]]:  # type: ignore[misc]
            u["title"] = names[u["task_id"]]
        plan["date"], plan["events"] = day, [self.event_view(e, tz) for e in evs]
        return plan

    async def briefing(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Morning briefing: today's schedule, the plan, habits still open, due reminders, goals needing attention."""
        today = await self.today(user_id)
        plan = await self.plan(user_id, today, max_tasks=5)
        habits = [
            h
            for h in [await self.habit_view(user_id, x.id) for x in await self.habits(user_id)]
            if h.get("due_today", True) and not h["done_today"]
        ]
        goals = [
            g
            for g in (self.goal_view(x, today) for x in await self.goals(user_id, "active"))
            if g["progress_state"] in ("at_risk", "overdue")
        ]
        _, d1, *_ = await self._day_bounds(user_id, today)
        reminders = [r for r in await self.due_reminders(user_id) if r["remind_at"] < d1]
        at_risk = [h["title"] for h in habits if (h.get("current_streak") or 0) >= 3]
        return {
            "date": today,
            "events": plan["events"],
            "plan": {
                k: plan[k]
                for k in ("blocks", "unscheduled", "overloaded", "planned_minutes", "free_minutes")
            },
            "habits_open": [
                {"id": h["id"], "title": h["title"], "current_streak": h["current_streak"]}
                for h in habits
            ],
            "streaks_at_risk": at_risk,
            "goals_needing_attention": [
                {
                    "id": g["id"],
                    "title": g["title"],
                    "state": g["progress_state"],
                    "days_left": g["progress_days_left"],
                }
                for g in goals
            ],
            "reminders_due": reminders,
            "mood_trend": await self.mood_trend(user_id, today),
        }

    async def review(self, user_id: uuid.UUID) -> dict[str, Any]:
        """Evening review: what got done, what carries over, habits and streaks, and tomorrow's first steps."""
        today = await self.today(user_id)
        d0, d1, *_ = await self._day_bounds(user_id, today)
        done_today = (
            await self._s.execute(
                select(func.count())
                .select_from(CoachTask)
                .where(
                    CoachTask.user_id == user_id,
                    CoachTask.status == "done",
                    CoachTask.completed_at >= d0,
                    CoachTask.completed_at < d1,
                )
            )
        ).scalar_one()
        views = [await self.habit_view(user_id, h.id) for h in await self.habits(user_id)]
        due = [h for h in views if h.get("due_today", True)]
        carry = [
            t
            for t in await self._open_tasks(user_id, today)
            if t.due_date is not None and t.due_date <= today
        ]
        tomorrow = await self.plan(user_id, today + timedelta(days=1), max_tasks=3)
        return {
            "date": today,
            "tasks_completed_today": done_today,
            "carry_over": [{"id": t.id, "title": t.title, "due_date": t.due_date} for t in carry],
            "habits_done": sum(1 for h in due if h["done_today"]),
            "habits_due": len(due),
            "streaks": [
                {"title": h["title"], "current_streak": h["current_streak"]} for h in views
            ],
            "mood_trend": await self.mood_trend(user_id, today),
            "tomorrow": {
                "first_events": tomorrow["events"][:3],
                "top_tasks": [
                    {"title": b["title"], "start": b["start"]} for b in tomorrow["blocks"][:3]
                ],
            },
        }

    async def progress(self, user_id: uuid.UUID, days: int = 7) -> dict[str, Any]:
        today = await self.today(user_id)
        d0, *_ = await self._day_bounds(user_id, today - timedelta(days=days - 1))
        done = (
            await self._s.execute(
                select(func.count())
                .select_from(CoachTask)
                .where(
                    CoachTask.user_id == user_id,
                    CoachTask.status == "done",
                    CoachTask.completed_at >= d0,
                )
            )
        ).scalar_one()
        open_n = len(await self._open_tasks(user_id, today))
        habits = [await self.habit_view(user_id, h.id) for h in await self.habits(user_id)]
        goals = [self.goal_view(g, today) for g in await self.goals(user_id, "active")]
        return {
            "window_days": days,
            "tasks_completed": done,
            "tasks_open": open_n,
            "habits": [
                {
                    "title": h["title"],
                    "current_streak": h["current_streak"],
                    "completion_rate_30d": h["completion_rate_30d"],
                }
                for h in habits
            ],
            "goals": [
                {
                    "title": g["title"],
                    "progress_pct": g["progress_pct"],
                    "state": g["progress_state"],
                }
                for g in goals
            ],
            "mood_trend": await self.mood_trend(user_id, today, days),
        }

    async def erase(self, user_id: uuid.UUID) -> None:
        for model in (
            CoachReminder,
            CoachCheckin,
            CoachHabitLog,
            CoachHabit,
            CoachEvent,
            CoachTask,
            CoachGoal,
            CoachSettings,
        ):
            await self._s.execute(delete(model).where(model.user_id == user_id))


__all__ = ["CoachError", "CoachService", "update"]
