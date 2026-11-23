"""Deterministic planning, habit and goal logic. Pure functions: no database, no model, no clock (callers pass
`today`/times). The same inputs always give the same plan, which is what makes it testable and trustworthy."""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta

DEFAULT_ESTIMATE = 30
OVERLOAD_RATIO = 0.8


@dataclass(frozen=True)
class TaskView:
    id: str
    title: str
    priority: int  # 1 (most important) .. 4
    due_date: date | None
    estimate_minutes: int | None
    goal_active: bool = False
    goal_days_left: int | None = None
    created_order: int = 0


def priority_score(t: TaskView, today: date) -> int:
    """Higher = do sooner. Components are additive and each is documented:
    importance 40/30/20/10 for priority 1..4; urgency: overdue 50 (+1/day overdue, max +10), today 45, tomorrow 30,
    within 7 days 15, later 5, none 0; +10 if it serves an active goal (+5 more if that goal is due within 14 days);
    +5 for a quick win (<= 15 minutes)."""
    score = {1: 40, 2: 30, 3: 20, 4: 10}[t.priority]
    if t.due_date is not None:
        delta = (t.due_date - today).days
        score += (
            50 + min(-delta, 10)
            if delta < 0
            else 45
            if delta == 0
            else 30
            if delta == 1
            else 15
            if delta <= 7
            else 5
        )
    if t.goal_active:
        score += 10 + (5 if t.goal_days_left is not None and 0 <= t.goal_days_left <= 14 else 0)
    if (t.estimate_minutes or DEFAULT_ESTIMATE) <= 15:
        score += 5
    return score


def rank_tasks(tasks: list[TaskView], today: date) -> list[tuple[TaskView, int]]:
    return sorted(
        ((t, priority_score(t, today)) for t in tasks),
        key=lambda p: (-p[1], p[0].due_date or date.max, p[0].created_order, p[0].id),
    )


@dataclass(frozen=True)
class Window:
    start: datetime
    end: datetime

    @property
    def minutes(self) -> int:
        return int((self.end - self.start).total_seconds() // 60)


def free_windows(
    day_start: datetime,
    day_end: datetime,
    busy: list[tuple[datetime, datetime]],
    buffer_minutes: int = 0,
) -> list[Window]:
    """Working window minus busy intervals (each padded by `buffer_minutes`), merged; windows < 15 min are dropped."""
    pad = timedelta(minutes=buffer_minutes)
    blocks = sorted(
        (max(s - pad, day_start), min(e + pad, day_end))
        for s, e in busy
        if e > day_start and s < day_end
    )
    out: list[Window] = []
    cursor = day_start
    for s, e in blocks:
        if s > cursor:
            out.append(Window(cursor, s))
        cursor = max(cursor, e)
    if cursor < day_end:
        out.append(Window(cursor, day_end))
    return [w for w in out if w.minutes >= 15]


def build_plan(
    ranked: list[tuple[TaskView, int]], windows: list[Window], max_tasks: int = 8
) -> dict[str, object]:
    """Greedy first-fit in priority order. A task is placed in the earliest window that fits its estimate; what does
    not fit is reported as unscheduled (never silently dropped, never squeezed into a too-small slot)."""
    free = [[w.start, w.end] for w in windows]
    blocks: list[dict[str, object]] = []
    unscheduled: list[dict[str, object]] = []
    planned_total = 0
    for t, score in ranked[:max_tasks]:
        need = timedelta(minutes=t.estimate_minutes or DEFAULT_ESTIMATE)
        slot = next((f for f in free if f[1] - f[0] >= need), None)
        if slot is None:
            unscheduled.append({"task_id": t.id, "score": score, "reason": "no_free_window"})
            continue
        blocks.append({"task_id": t.id, "start": slot[0], "end": slot[0] + need, "score": score})
        slot[0] += need
        planned_total += int(need.total_seconds() // 60)
    deferred = [
        {"task_id": t.id, "score": s, "reason": "beyond_daily_limit"} for t, s in ranked[max_tasks:]
    ]
    free_total = sum(w.minutes for w in windows)
    planned = planned_total
    return {
        "blocks": blocks,
        "unscheduled": unscheduled,
        "deferred": deferred,
        "free_minutes": free_total,
        "planned_minutes": planned,
        "overloaded": bool(free_total)
        and planned >= OVERLOAD_RATIO * free_total
        or bool(unscheduled),
    }


# ------------------------------------------------------------------------------------------------ habits
def _due_day(cadence: str, d: date) -> bool:
    return cadence == "daily" or (cadence == "weekdays" and d.weekday() < 5) or cadence == "weekly"


def habit_stats(
    cadence: str, target_per_week: int, logs: set[date], today: date
) -> dict[str, object]:
    """Daily/weekdays: streak = consecutive DUE days with a log, ending today - or yesterday if today is not logged
    yet (today is still open, so it does not break the streak). Weekly: streak = consecutive past ISO weeks that
    met the target. Completion rate = done/due over the last 30 days."""
    if cadence == "weekly":

        def week_key(d: date) -> tuple[int, int]:
            iso = d.isocalendar()
            return iso.year, iso.week

        counts: dict[tuple[int, int], int] = {}
        for d in logs:
            counts[week_key(d)] = counts.get(week_key(d), 0) + 1
        cur, wk = (
            0,
            today - timedelta(days=7),
        )  # last COMPLETED week backwards; this week is still open
        this_week_met = counts.get(week_key(today), 0) >= target_per_week
        if this_week_met:
            cur += 1
        while counts.get(week_key(wk), 0) >= target_per_week:
            cur += 1
            wk -= timedelta(days=7)
        return {
            "current_streak": cur,
            "unit": "weeks",
            "this_week": counts.get(week_key(today), 0),
            "target_per_week": target_per_week,
            "done_today": today in logs,
            "completion_rate_30d": None,
        }
    cur, d = 0, today if today in logs else today - timedelta(days=1)
    while True:
        if not _due_day(cadence, d):
            d -= timedelta(days=1)
            continue
        if d in logs:
            cur += 1
            d -= timedelta(days=1)
        else:
            break
        if (today - d).days > 4000:
            break
    due30 = [
        today - timedelta(days=i) for i in range(30) if _due_day(cadence, today - timedelta(days=i))
    ]
    rate = round(sum(1 for x in due30 if x in logs) / len(due30) * 100, 1) if due30 else None
    longest = run = 0
    prev: date | None = None
    for x in sorted(logs):
        gap_ok = prev is not None and all(
            not _due_day(cadence, prev + timedelta(days=k)) for k in range(1, (x - prev).days)
        )
        run = run + 1 if prev is not None and (x - prev).days >= 1 and gap_ok else 1
        longest, prev = max(longest, run), x
    return {
        "current_streak": cur,
        "longest_streak": longest,
        "unit": "days",
        "done_today": today in logs,
        "due_today": _due_day(cadence, today),
        "completion_rate_30d": rate,
    }


# ------------------------------------------------------------------------------------------------ goals
def goal_status(
    progress_pct: int, created: date, target: date | None, today: date, status: str = "active"
) -> dict[str, object]:
    """on_track / at_risk / overdue. Expected progress is LINEAR between creation and the target date; a goal is at
    risk when it is more than 15 points behind that line. No target date => no judgement (status 'no_deadline')."""
    if status != "active":
        return {"state": status, "expected_pct": None, "days_left": None}
    if target is None:
        return {"state": "no_deadline", "expected_pct": None, "days_left": None}
    days_left = (target - today).days
    total = max((target - created).days, 1)
    expected = min(100, max(0, round((today - created).days / total * 100)))
    state = (
        "overdue" if days_left < 0 else "at_risk" if progress_pct < expected - 15 else "on_track"
    )
    return {"state": state, "expected_pct": expected, "days_left": days_left}


def combine(day: date, t: time, tz: object) -> datetime:
    return datetime.combine(day, t, tzinfo=tz)  # type: ignore[arg-type]
