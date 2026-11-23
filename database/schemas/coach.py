"""Daily Coach tables (Milestone 7c): goals, tasks, habits, calendar events, reminders, check-ins.
All user-owned (ON DELETE CASCADE). Titles and notes are envelope-encrypted per field; dates, priorities
and statuses stay plaintext because planning/streak maths and indexes need them."""

import uuid
from datetime import date, datetime, time

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Time,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from database.schemas.base import Base
from database.schemas.columns import created_at_col, uuid_pk


def _user() -> Mapped[uuid.UUID]:
    return mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)


class CoachSettings(Base):
    __tablename__ = "coach_settings"
    __table_args__ = (CheckConstraint("work_start < work_end", name="work_hours_valid"),)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    timezone: Mapped[str] = mapped_column(
        String(48), default="Asia/Kolkata", server_default="Asia/Kolkata"
    )
    work_start: Mapped[time] = mapped_column(Time, default=time(9, 0), server_default="09:00")
    work_end: Mapped[time] = mapped_column(Time, default=time(18, 0), server_default="18:00")
    buffer_minutes: Mapped[int] = mapped_column(Integer, default=10, server_default="10")


class CoachGoal(Base):
    __tablename__ = "coach_goals"
    __table_args__ = (
        CheckConstraint("status IN ('active','done','paused','dropped')", name="status_valid"),
        CheckConstraint("progress_pct BETWEEN 0 AND 100", name="progress_valid"),
        CheckConstraint(
            "category IN ('health','career','finance','learning','relationships','personal')",
            name="category_valid",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    category: Mapped[str] = mapped_column(String(16), default="personal", server_default="personal")
    target_date: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(8), default="active", server_default="active")
    progress_pct: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoachTask(Base):
    __tablename__ = "coach_tasks"
    __table_args__ = (
        CheckConstraint("priority BETWEEN 1 AND 4", name="priority_valid"),
        CheckConstraint(
            "estimate_minutes IS NULL OR estimate_minutes BETWEEN 5 AND 480", name="estimate_valid"
        ),
        CheckConstraint("status IN ('todo','done','snoozed','dropped')", name="status_valid"),
        Index("ix_coach_tasks_user_status_due", "user_id", "status", "due_date"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    due_date: Mapped[date | None] = mapped_column(Date)
    priority: Mapped[int] = mapped_column(
        Integer, default=3, server_default="3"
    )  # 1 = most important
    estimate_minutes: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(8), default="todo", server_default="todo")
    snoozed_until: Mapped[date | None] = mapped_column(Date)
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coach_goals.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CoachHabit(Base):
    __tablename__ = "coach_habits"
    __table_args__ = (
        CheckConstraint("cadence IN ('daily','weekdays','weekly')", name="cadence_valid"),
        CheckConstraint("target_per_week BETWEEN 1 AND 7", name="target_valid"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    cadence: Mapped[str] = mapped_column(String(8), default="daily", server_default="daily")
    target_per_week: Mapped[int] = mapped_column(Integer, default=7, server_default="7")
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    goal_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coach_goals.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = created_at_col()


class CoachHabitLog(Base):
    __tablename__ = "coach_habit_logs"
    __table_args__ = (UniqueConstraint("habit_id", "log_date", name="uq_coach_habit_day"),)
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    habit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("coach_habits.id", ondelete="CASCADE"))
    log_date: Mapped[date] = mapped_column(Date)


class CoachEvent(Base):
    __tablename__ = "coach_events"
    __table_args__ = (
        CheckConstraint("end_at >= start_at", name="event_range_valid"),
        CheckConstraint("source IN ('manual','ics','shortcut')", name="source_valid"),
        Index("ix_coach_events_user_start", "user_id", "start_at"),
        Index(
            "uq_coach_events_user_external",
            "user_id",
            "external_hash",
            unique=True,
            postgresql_where="external_hash IS NOT NULL",
        ),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    title_enc: Mapped[bytes] = mapped_column(LargeBinary)
    start_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    all_day: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    source: Mapped[str] = mapped_column(String(8), default="manual", server_default="manual")
    external_hash: Mapped[str | None] = mapped_column(String(64))
    reminder_minutes: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = created_at_col()


class CoachReminder(Base):
    __tablename__ = "coach_reminders"
    __table_args__ = (
        CheckConstraint("status IN ('pending','delivered','dismissed')", name="status_valid"),
        Index("ix_coach_reminders_user_due", "user_id", "status", "remind_at"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    text_enc: Mapped[bytes] = mapped_column(LargeBinary)
    remind_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(10), default="pending", server_default="pending")
    event_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coach_events.id", ondelete="CASCADE")
    )
    task_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("coach_tasks.id", ondelete="CASCADE")
    )
    delivered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = created_at_col()


class CoachCheckin(Base):
    __tablename__ = "coach_checkins"
    __table_args__ = (
        UniqueConstraint("user_id", "checkin_date", "kind", name="uq_coach_checkin"),
        CheckConstraint("kind IN ('morning','evening')", name="kind_valid"),
        CheckConstraint("mood BETWEEN 1 AND 5 AND energy BETWEEN 1 AND 5", name="scale_valid"),
    )
    id: Mapped[uuid.UUID] = uuid_pk()
    user_id: Mapped[uuid.UUID] = _user()
    checkin_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(8))
    mood: Mapped[int] = mapped_column(Integer)
    energy: Mapped[int] = mapped_column(Integer)
    note_enc: Mapped[bytes | None] = mapped_column(LargeBinary)
