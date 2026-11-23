# mypy: disable-error-code="attr-defined,index,union-attr,arg-type"
"""Deterministic coach logic (planning, streaks, goals, ICS). Expected values are worked out by hand."""

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

import pytest

from domain.coach import planning as pl
from domain.coach.ics import IcsError, parse_ics

IST = ZoneInfo("Asia/Kolkata")
TODAY = date(2024, 3, 12)  # a Tuesday


def task(
    i: str,
    prio: int,
    due: date | None = None,
    est: int | None = 30,
    goal: bool = False,
    days_left: int | None = None,
    order: int = 0,
) -> pl.TaskView:
    return pl.TaskView(i, i, prio, due, est, goal, days_left, order)


def test_priority_score_components() -> None:
    # priority 1 (40) + due today (45) = 85
    assert pl.priority_score(task("a", 1, TODAY), TODAY) == 85
    # priority 3 (20) + overdue by 3 days (50+3) = 73
    assert pl.priority_score(task("b", 3, TODAY - timedelta(days=3)), TODAY) == 73
    # overdue bonus is capped at +10: 20 + 50 + 10 = 80
    assert pl.priority_score(task("c", 3, TODAY - timedelta(days=99)), TODAY) == 80
    # priority 2 (30) + goal (10) + goal due soon (5) + quick win (5) + due in 5 days (15) = 65
    assert (
        pl.priority_score(
            task("d", 2, TODAY + timedelta(days=5), est=10, goal=True, days_left=10), TODAY
        )
        == 65
    )
    assert pl.priority_score(task("e", 4), TODAY) == 10  # 10 + nothing


def test_ranking_is_deterministic_with_ties() -> None:
    ts = [task("z", 2, order=3), task("a", 2, order=1), task("m", 2, order=2)]
    assert [t.id for t, _ in pl.rank_tasks(ts, TODAY)] == ["a", "m", "z"]
    assert [t.id for t, _ in pl.rank_tasks(list(reversed(ts)), TODAY)] == ["a", "m", "z"]


def dt(h: int, m: int = 0) -> datetime:
    return datetime(2024, 3, 12, h, m, tzinfo=IST)


def test_free_windows_with_buffer() -> None:
    w = pl.free_windows(dt(9), dt(18), [(dt(10), dt(11)), (dt(13), dt(14))], buffer_minutes=10)
    assert [(x.start.strftime("%H:%M"), x.end.strftime("%H:%M")) for x in w] == [
        ("09:00", "09:50"),
        ("11:10", "12:50"),
        ("14:10", "18:00"),
    ]
    assert pl.free_windows(dt(9), dt(10), [(dt(8), dt(11))]) == []  # fully booked
    assert (
        pl.free_windows(dt(9), dt(10), [(dt(9, 10), dt(9, 50))]) == []
    )  # leftover slivers under 15 min are dropped


def test_build_plan_first_fit_and_overflow() -> None:
    windows = pl.free_windows(dt(9), dt(12), [(dt(10), dt(10, 30))])  # 09:00-10:00 and 10:30-12:00
    ranked = pl.rank_tasks(
        [
            task("big", 1, TODAY, est=90),
            task("mid", 2, TODAY, est=45),
            task("small", 3, est=20),
            task("huge", 4, est=200),
        ],
        TODAY,
    )
    plan = pl.build_plan(ranked, windows)
    starts = {b["task_id"]: b["start"].strftime("%H:%M") for b in plan["blocks"]}
    assert starts == {
        "big": "10:30",
        "mid": "09:00",
    }  # big only fits the 90-min window; mid takes 09:00-09:45
    # small (20 min) does NOT fit the 15 minutes left in the first window, and is not squeezed in
    assert [u["task_id"] for u in plan["unscheduled"]] == ["small", "huge"]
    assert plan["planned_minutes"] == 135 and plan["free_minutes"] == 150
    assert plan["overloaded"] is True  # 135 >= 80% of 150
    capped = pl.build_plan(ranked, windows, max_tasks=1)
    assert len(capped["blocks"]) == 1 and len(capped["deferred"]) == 3


def logs(*days: int) -> set[date]:
    return {TODAY - timedelta(days=d) for d in days}


def test_daily_streak_semantics() -> None:
    assert pl.habit_stats("daily", 7, logs(0, 1, 2), TODAY)["current_streak"] == 3
    assert (
        pl.habit_stats("daily", 7, logs(1, 2, 3), TODAY)["current_streak"] == 3
    )  # today still open: not broken
    assert (
        pl.habit_stats("daily", 7, logs(2, 3), TODAY)["current_streak"] == 0
    )  # yesterday missed: broken
    s = pl.habit_stats("daily", 7, logs(0, 1, 2, 4, 5, 6, 7, 8), TODAY)
    assert s["current_streak"] == 3 and s["longest_streak"] == 5 and s["done_today"] is True
    assert s["completion_rate_30d"] == round(8 / 30 * 100, 1)


def test_weekday_habit_skips_weekends() -> None:
    mon = date(2024, 3, 11)  # Monday; today is Tuesday
    friday, thursday = mon - timedelta(days=3), mon - timedelta(days=4)
    s = pl.habit_stats("weekdays", 5, {TODAY, mon, friday, thursday}, TODAY)
    assert s["current_streak"] == 4  # Tue, Mon, Fri, Thu: the weekend is not a miss


def test_weekly_habit() -> None:
    this_week = {TODAY, TODAY - timedelta(days=1)}
    last_week = {TODAY - timedelta(days=6), TODAY - timedelta(days=7), TODAY - timedelta(days=8)}
    s = pl.habit_stats("weekly", 3, this_week | last_week, TODAY)
    assert (
        s["this_week"] == 2 and s["current_streak"] == 1 and s["unit"] == "weeks"
    )  # last week met 3, this week not yet


def test_goal_status() -> None:
    created, target = date(2024, 1, 1), date(2024, 4, 10)  # 100 days
    assert (
        pl.goal_status(60, created, target, date(2024, 2, 20))["state"] == "on_track"
    )  # 50% elapsed, 60% done
    r = pl.goal_status(20, created, target, date(2024, 2, 20))
    assert r["state"] == "at_risk" and r["expected_pct"] == 50 and r["days_left"] == 50
    assert pl.goal_status(99, created, target, date(2024, 4, 11))["state"] == "overdue"
    assert pl.goal_status(10, created, None, TODAY)["state"] == "no_deadline"
    assert pl.goal_status(100, created, target, TODAY, "done")["state"] == "done"


ICS = b"""BEGIN:VCALENDAR\r
VERSION:2.0\r
BEGIN:VEVENT\r
UID:abc-1\r
SUMMARY:Team sync\\, weekly\r
DTSTART:20240312T043000Z\r
DTEND:20240312T050000Z\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:abc-2\r
SUMMARY:Dentist with a very long title that is
  folded across lines\r
DTSTART;TZID=Asia/Kolkata:20240313T153000\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:abc-3\r
SUMMARY:Holiday\r
DTSTART;VALUE=DATE:20240314\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:rec\r
SUMMARY:Standup\r
DTSTART:20240312T040000Z\r
RRULE:FREQ=DAILY\r
END:VEVENT\r
BEGIN:VEVENT\r
UID:bad\r
SUMMARY:Broken\r
DTSTART:garbage\r
END:VEVENT\r
END:VCALENDAR\r
"""


def test_ics_parsing() -> None:
    r = parse_ics(ICS, IST)
    assert (
        [e.title for e in r.events][0] == "Team sync, weekly"
        and r.skipped_recurring == 1
        and r.skipped_invalid == 1
    )
    e1, e2, e3 = r.events
    assert e1.start == datetime(2024, 3, 12, 4, 30, tzinfo=UTC) and e1.end - e1.start == timedelta(
        minutes=30
    )
    assert (
        e2.start.hour == 15
        and e2.end - e2.start == timedelta(hours=1)
        and "folded across lines" in e2.title
    )
    assert e3.all_day is True and e3.end - e3.start == timedelta(days=1)


@pytest.mark.parametrize("data", [b"hello", b"\xff\xfe\x00bad", b"x" * 600_000])
def test_ics_rejects_garbage(data: bytes) -> None:
    with pytest.raises(IcsError):
        parse_ics(data, IST)


def test_combine_helper() -> None:
    assert pl.combine(TODAY, time(9, 0), IST).utcoffset() == timedelta(hours=5, minutes=30)
