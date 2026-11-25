"""Structured JSON logging with redaction (P1.6). Opt-in: ABU_LOG_FORMAT=json (the staging/production guard requires it).
The default (text) leaves existing logging untouched.

Redaction is applied to the FINAL text of every record, including exception tracebacks, so a secret that reaches a log
call by accident is still not written. It is a safety net, not a licence to log secrets: the code never logs request or
response bodies, headers, query strings, statement rows, message text or memory contents (tests assert this).
"""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from typing import Any

# Order matters: specific shapes first, generic key=value last.
_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]{8,}"), r"\1 [redacted]"),
    (re.compile(r"\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]*"), "[redacted-jwt]"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{8,}"), "[redacted-key]"),
    (re.compile(r"\b(AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,})\b"), "[redacted-key]"),
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)", re.S
        ),
        "[redacted-private-key]",
    ),
    # credentials inside a URL: scheme://user:password@host
    (re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://[^\s/:@]+):([^\s@/]+)@"), r"\1:[redacted]@"),
    # key=value / "key": "value" for secret-looking keys, including compound names (refresh_token, jwt_secret, api_key).
    # The very short key words are matched only as a whole key, so "shipping=..." or "author=..." stay readable.
    (
        re.compile(
            r"(?i)(\"?(?:[\w.-]*(?:password|passwd|secret|token|api[_-]?key|authorization|cookie|credential|"
            r"private[_-]?key|keyring)[\w.-]*|\b(?:pwd|auth|kek|otp|pin)\b)\"?\s*[:=]\s*)"
            r"(\"[^\"]*\"|'[^']*'|[^\s,;}&]+)"
        ),
        r"\1[redacted]",
    ),
    # long digit runs: account, card and phone numbers (also catches IDs, which is acceptable in a log)
    (re.compile(r"(?<![\w.])\d[\d -]{7,}\d(?![\w.])"), "[digits]"),
    # e-mail addresses
    (re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"), "[email]"),
]


def redact(text: str) -> str:
    for pattern, repl in _PATTERNS:
        text = pattern.sub(repl, text)
    return text


def _redact_obj(value: Any) -> Any:
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        return {k: _redact_obj(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_redact_obj(v) for v in value]
    return value


class JsonFormatter(logging.Formatter):
    """One JSON object per line. Structured fields come from `extra={"abu": {...}}`."""

    def format(self, record: logging.LogRecord) -> str:
        doc: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, UTC).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        extra = getattr(record, "abu", None)
        if isinstance(extra, dict):
            doc.update(extra)
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(_redact_obj(doc), separators=(",", ":"), default=str)


def configure_logging(level: str = "INFO", stream: Any = None) -> None:
    """Route everything (including uvicorn's own loggers) through the JSON formatter. Idempotent."""
    handler = logging.StreamHandler(stream or sys.stdout)
    handler.setFormatter(JsonFormatter())
    handler.set_name("abu-json")
    root = logging.getLogger()
    root.handlers = [h for h in root.handlers if h.get_name() != "abu-json"] + [handler]
    root.setLevel(level.upper())
    for name in ("uvicorn", "uvicorn.error"):
        lg = logging.getLogger(name)
        lg.handlers = []
        lg.propagate = True
    access = logging.getLogger(
        "uvicorn.access"
    )  # replaced by the application's own, credential-free access log
    access.handlers = []
    access.propagate = False
    access.disabled = True


def event(logger: logging.Logger, level: int, message: str, **fields: Any) -> None:
    logger.log(level, message, extra={"abu": fields})
