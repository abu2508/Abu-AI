"""Conversation import: turns text the USER chose to share into messages. No platform API is assumed -
WhatsApp/Instagram/Telegram personal accounts offer none for this - so the inputs are an exported chat
file, pasted text with speaker labels, or a small JSON list from a Shortcut/Share Extension."""

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime

MAX_BYTES = 1_000_000
MAX_MESSAGES = 5000
MAX_BODY = 4000
_CTRL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f​-‏‪-‮⁦-⁩]")


class ImportError_(Exception):
    """Safe code only (no content)."""


@dataclass(frozen=True)
class ParsedMessage:
    sender: str  # me | them
    sent_at: datetime
    text: str


def clean(text: str) -> str:
    t = unicodedata.normalize("NFKC", _CTRL.sub("", text)).strip()
    return t[:MAX_BODY]


_WA = re.compile(
    r"^\[?(?P<d>\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}),?\s+(?P<t>\d{1,2}:\d{2}(?::\d{2})?\s?(?:[APap][Mm])?)\]?\s*[-–]?\s*(?P<who>[^:]{1,60}):\s?(?P<msg>.*)$"
)


def _when(d: str, t: str) -> datetime:
    t = t.strip().lower().replace(" ", "")
    for dfmt in ("%d/%m/%Y", "%d/%m/%y", "%d.%m.%Y", "%d.%m.%y", "%d-%m-%Y", "%d-%m-%y"):
        for tfmt in ("%I:%M%p", "%I:%M:%S%p", "%H:%M", "%H:%M:%S"):
            try:
                return datetime.strptime(f"{d} {t}", f"{dfmt} {tfmt}").replace(tzinfo=UTC)
            except ValueError:
                continue
    raise ImportError_("bad_timestamp")


def parse_whatsapp_export(text: str, my_name: str) -> list[ParsedMessage]:
    """'12/03/24, 9:41 pm - Asha: hello' and '[12/03/2024, 21:41:05] Asha: hello', with multi-line messages.
    `my_name` (as it appears in the export) decides which speaker is 'me'; everyone else is 'them'."""
    me = my_name.strip().casefold()
    if not me:
        raise ImportError_("my_name_required")
    out: list[ParsedMessage] = []
    cur: list[str] | None = None
    meta: tuple[str, datetime] | None = None

    def flush() -> None:
        if meta and cur is not None and (body := clean("\n".join(cur))):
            out.append(ParsedMessage(meta[0], meta[1], body))

    for line in text.splitlines():
        m = _WA.match(line.strip("‎"))
        if m:
            flush()
            who = m.group("who").strip().casefold()
            try:
                meta = ("me" if who == me else "them", _when(m.group("d"), m.group("t")))
            except ImportError_:
                meta, cur = None, None
                continue
            cur = [m.group("msg")]
        elif cur is not None:
            cur.append(line)
        if len(out) > MAX_MESSAGES:
            raise ImportError_("too_many_messages")
    flush()
    return out


_LABELLED = re.compile(r"^(?P<who>me|you|them|they|other)\s*:\s?(?P<msg>.*)$", re.I)


def parse_labelled(text: str, base: datetime) -> list[ParsedMessage]:
    """Pasted text like 'me: hi' / 'them: hello'. No timestamps exist, so messages are spaced one minute apart
    ending at `base` (order is preserved; response-time statistics are then meaningless and are not reported)."""
    items: list[tuple[str, list[str]]] = []
    for line in text.splitlines():
        m = _LABELLED.match(line.strip())
        if m:
            items.append(
                ("me" if m.group("who").lower() in ("me", "you") else "them", [m.group("msg")])
            )
        elif items and line.strip():
            items[-1][1].append(line)
    if len(items) > MAX_MESSAGES:
        raise ImportError_("too_many_messages")
    n = len(items)
    from datetime import timedelta

    return [
        ParsedMessage(s, base - timedelta(minutes=n - i), body)
        for i, (s, parts) in enumerate(items)
        if (body := clean("\n".join(parts)))
    ]


def parse_json(text: str) -> list[ParsedMessage]:
    try:
        data = json.loads(text)
    except ValueError:
        raise ImportError_("invalid_json") from None
    if not isinstance(data, list) or len(data) > MAX_MESSAGES:
        raise ImportError_("invalid_json")
    out = []
    for it in data:
        if (
            not isinstance(it, dict)
            or it.get("sender") not in ("me", "them")
            or not isinstance(it.get("text"), str)
        ):
            raise ImportError_("invalid_message")
        try:
            ts = datetime.fromisoformat(str(it["sent_at"]).replace("Z", "+00:00"))
        except (KeyError, ValueError):
            raise ImportError_("bad_timestamp") from None
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        if body := clean(it["text"]):
            out.append(ParsedMessage(it["sender"], ts, body))
    return out


def parse(fmt: str, text: str, *, my_name: str | None, now: datetime) -> list[ParsedMessage]:
    if len(text.encode()) > MAX_BYTES:
        raise ImportError_("too_large")
    msgs = (
        parse_whatsapp_export(text, my_name or "")
        if fmt == "whatsapp_export"
        else parse_labelled(text, now)
        if fmt == "labelled"
        else parse_json(text)
        if fmt == "json"
        else (_ for _ in ()).throw(ImportError_("invalid_format"))
    )
    if not msgs:
        raise ImportError_("no_messages")
    future = [m for m in msgs if (m.sent_at - now).days > 1]
    if future:
        raise ImportError_("future_timestamp")
    return sorted(msgs, key=lambda m: m.sent_at)
