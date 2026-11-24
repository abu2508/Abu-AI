"""Coach response shapes. Sources: `domain/coach/service.py`, `domain/coach/planning.py`, `agents/tools.py`."""

from typing import Annotated, Literal

from pydantic import Field

from services.api.contracts.common import Contract, IsoDate, IsoDateTime, UuidStr


class EventOut(Contract):
    id: UuidStr
    title: str
    start: IsoDateTime
    end: IsoDateTime
    all_day: bool
    source: str
    reminder_minutes: int | None


class BlockOut(Contract):
    task_id: UuidStr
    start: IsoDateTime
    end: IsoDateTime
    score: int
    title: str


class UnscheduledOut(Contract):
    task_id: UuidStr
    score: int
    reason: Literal["no_free_window", "beyond_daily_limit"]
    title: str


class ReminderOut(Contract):
    id: UuidStr
    text: str
    remind_at: IsoDateTime
    event_id: UuidStr | None
    task_id: UuidStr | None


class MoodTrend(Contract):
    checkins: int
    avg_mood: float | None
    avg_energy: float | None


class BriefingPlan(Contract):
    """The briefing's plan: unlike `coach.plan` it has no `deferred` list."""

    blocks: list[BlockOut]
    unscheduled: list[UnscheduledOut]
    overloaded: bool
    planned_minutes: int
    free_minutes: int


class HabitOpen(Contract):
    id: UuidStr
    title: str
    current_streak: int


class GoalAttention(Contract):
    id: UuidStr
    title: str
    state: str
    days_left: int | None


class CoachBriefing(Contract):
    """GET /v1/coach/briefing and tool `coach.briefing` (identical)."""

    date: IsoDate
    events: list[EventOut]
    plan: BriefingPlan
    habits_open: list[HabitOpen]
    streaks_at_risk: list[str]
    goals_needing_attention: list[GoalAttention]
    reminders_due: list[ReminderOut]
    mood_trend: MoodTrend


class PlanTool(Contract):
    date: IsoDate
    events: list[EventOut]
    blocks: list[BlockOut]
    unscheduled: list[UnscheduledOut]
    deferred: list[UnscheduledOut]
    free_minutes: int
    planned_minutes: int
    overloaded: bool


class CarryOver(Contract):
    id: UuidStr
    title: str
    due_date: IsoDate | None


class StreakRow(Contract):
    title: str
    current_streak: int


class TopTask(Contract):
    title: str
    start: IsoDateTime


class Tomorrow(Contract):
    first_events: list[EventOut]
    top_tasks: list[TopTask]


class ReviewTool(Contract):
    date: IsoDate
    tasks_completed_today: int
    carry_over: list[CarryOver]
    habits_done: int
    habits_due: int
    streaks: list[StreakRow]
    mood_trend: MoodTrend
    tomorrow: Tomorrow


class ProgressHabit(Contract):
    title: str
    current_streak: int
    completion_rate_30d: float | None


class ProgressGoal(Contract):
    title: str
    progress_pct: int
    state: str


class ProgressTool(Contract):
    window_days: int
    tasks_completed: int
    tasks_open: int
    habits: list[ProgressHabit]
    goals: list[ProgressGoal]
    mood_trend: MoodTrend


class HabitDays(Contract):
    """Daily and weekday habits (`unit == "days"`)."""

    id: UuidStr
    title: str
    cadence: str
    target_per_week: int
    current_streak: int
    longest_streak: int
    unit: Literal["days"]
    done_today: bool
    due_today: bool
    completion_rate_30d: float | None


class HabitWeeks(Contract):
    """Weekly habits (`unit == "weeks"`): no `longest_streak` or `due_today`."""

    id: UuidStr
    title: str
    cadence: str
    target_per_week: int
    current_streak: int
    unit: Literal["weeks"]
    this_week: int
    done_today: bool
    completion_rate_30d: None


HabitView = Annotated[HabitDays | HabitWeeks, Field(discriminator="unit")]


class HabitsTool(Contract):
    habits: list[HabitView]


class GoalView(Contract):
    id: UuidStr
    title: str
    category: str
    status: str
    progress_pct: int
    target_date: IsoDate | None
    progress_state: str = Field(
        description="on_track, at_risk, overdue or no_deadline for active goals; otherwise the goal's own status"
    )
    progress_expected_pct: int | None
    progress_days_left: int | None


class GoalsTool(Contract):
    goals: list[GoalView]
