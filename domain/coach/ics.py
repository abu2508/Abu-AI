"""Minimal, defensive iCalendar (.ics) reader and the calendar/reminder provider architecture.

Architecture: calendar data enters through a `CalendarProvider` (today only a user-supplied .ics file; later
EventKit via the iOS app, or Google) and is upserted by a stable external hash, so re-syncing never duplicates.
Reminders leave through a `ReminderChannel` (today: the app polls /v1/coach/reminders/due; later push).
Nothing here requires or assumes access to the user's calendar or notifications.
"""

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo

MAX_BYTES = 500_000
MAX_EVENTS = 2000


class IcsError(Exception):
    pass


@dataclass(frozen=True)
class ExternalEvent:
    uid: str
    title: str
    start: datetime
    end: datetime
    all_day: bool


@dataclass
class IcsResult:
    events: list[ExternalEvent]
    skipped_recurring: int = 0
    skipped_invalid: int = 0


class CalendarProvider(Protocol):
    """Anything that can list events in a window. Implemented later by EventKit/Google adapters."""

    async def fetch(self, start: datetime, end: datetime) -> list[ExternalEvent]: ...


class ReminderChannel(Protocol):
    """How a due reminder reaches the user. The default is polling; push is a later, separate decision."""

    async def deliver(self, user_id: str, reminder_id: str) -> None: ...


def _unfold(text: str) -> list[str]:
    out: list[str] = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if line[:1] in (" ", "\t") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return out


def _unescape(v: str) -> str:
    return re.sub(r"\\([nN,;\\])", lambda m: "\n" if m.group(1) in "nN" else m.group(1), v)


def _parse_dt(value: str, params: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    v = value.strip()
    if "VALUE=DATE" in params.upper() or re.fullmatch(r"\d{8}", v):
        d = datetime.strptime(v[:8], "%Y%m%d").date()
        return datetime.combine(d, time(0), tzinfo=tz), True
    if v.endswith("Z"):
        return datetime.strptime(v, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC), False
    m = re.search(r"TZID=([^;:]+)", params)
    zone = tz
    if m:
        try:
            zone = ZoneInfo(m.group(1))
        except Exception:  # noqa: BLE001 - unknown zone name: fall back to the user's own zone
            zone = tz
    return datetime.strptime(v, "%Y%m%dT%H%M%S").replace(tzinfo=zone), False


def parse_ics(data: bytes, tz: ZoneInfo, today: date | None = None) -> IcsResult:
    if len(data) > MAX_BYTES:
        raise IcsError("too_large")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeError:
        raise IcsError("not_utf8") from None
    if "BEGIN:VCALENDAR" not in text:
        raise IcsError("not_icalendar")
    res = IcsResult([])
    cur: dict[str, tuple[str, str]] | None = None
    for line in _unfold(text):
        if line == "BEGIN:VEVENT":
            cur = {}
        elif line == "END:VEVENT" and cur is not None:
            ev, cur = cur, None
            if "RRULE" in ev:
                res.skipped_recurring += 1
                continue
            try:
                start, all_day = _parse_dt(ev["DTSTART"][1], ev["DTSTART"][0], tz)
                if "DTEND" in ev:
                    end, _ = _parse_dt(ev["DTEND"][1], ev["DTEND"][0], tz)
                else:
                    end = start + (timedelta(days=1) if all_day else timedelta(hours=1))
                if end < start:
                    raise ValueError
                title = (
                    _unescape(ev.get("SUMMARY", ("", "(no title)"))[1]).strip()[:200]
                    or "(no title)"
                )
                uid = ev.get("UID", ("", ""))[1].strip()[:200]
                res.events.append(
                    ExternalEvent(uid or f"{title}|{start.isoformat()}", title, start, end, all_day)
                )
            except (KeyError, ValueError):
                res.skipped_invalid += 1
            if len(res.events) > MAX_EVENTS:
                raise IcsError("too_many_events")
        elif cur is not None and ":" in line:
            head, _, val = line.partition(":")
            name, _, params = head.partition(";")
            cur.setdefault(name.upper(), (params, val))
    return res
