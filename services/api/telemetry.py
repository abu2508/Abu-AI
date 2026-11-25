"""Credential-free access logging and authentication-anomaly detection (P1.6). Pure ASGI, opt-in (ABU_LOG_FORMAT=json).

Logged per request: method, path SHAPE (identifiers collapsed), status, duration, request id. NEVER logged: query string,
headers, cookies, bodies, client address, user identity. An inbound X-Request-ID is accepted only if it is a short
token; otherwise a random one is generated, and it is echoed back so one request can be followed across components.
"""

import logging
import re
import time
import uuid
from collections import deque
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.api.logging_config import event

log = logging.getLogger("abu.access")
_ID = re.compile(
    r"/(?:[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}|\d{3,})(?=/|$)"
)
_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{8,64}$")
ANOMALY_STATUSES = {401: "unauthorised", 403: "forbidden", 429: "rate_limited"}


def path_shape(path: str) -> str:
    return _ID.sub("/{id}", path)[:200]


class AnomalyDetector:
    """Sliding-window counter of auth-related failures. Emits at most one warning per window while above the threshold."""

    def __init__(
        self, threshold: int, window_seconds: float = 60.0, clock: Any = time.monotonic
    ) -> None:
        self.threshold, self.window, self._clock = threshold, window_seconds, clock
        self._hits: deque[tuple[float, str]] = deque()
        self._last_alert = float("-inf")

    def record(self, kind: str) -> dict[str, int] | None:
        now = self._clock()
        self._hits.append((now, kind))
        while self._hits and now - self._hits[0][0] > self.window:
            self._hits.popleft()
        if len(self._hits) >= self.threshold and now - self._last_alert >= self.window:
            self._last_alert = now
            counts: dict[str, int] = {}
            for _, k in self._hits:
                counts[k] = counts.get(k, 0) + 1
            return counts
        return None


class AccessLogMiddleware:
    def __init__(
        self, app: ASGIApp, anomaly_threshold: int = 20, clock: Any = time.monotonic
    ) -> None:
        self.app = app
        self.detector = AnomalyDetector(anomaly_threshold, clock=clock)
        self._clock = clock

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        inbound = next(
            (v.decode("latin-1") for k, v in scope["headers"] if k == b"x-request-id"), ""
        )
        request_id = inbound if _REQUEST_ID.match(inbound) else uuid.uuid4().hex
        started = self._clock()
        status = 500

        async def tracking_send(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message = {
                    **message,
                    "headers": [
                        *message.get("headers", []),
                        (b"x-request-id", request_id.encode()),
                    ],
                }
            await send(message)

        try:
            await self.app(scope, receive, tracking_send)
        finally:
            event(
                log,
                logging.INFO if status < 500 else logging.ERROR,
                "http_request",
                event="http_request",
                method=scope["method"],
                path=path_shape(scope["path"]),
                status=status,
                duration_ms=round((self._clock() - started) * 1000, 1),
                request_id=request_id,
            )
            kind = ANOMALY_STATUSES.get(status)
            if kind is not None:
                counts = self.detector.record(kind)
                if counts is not None:
                    event(
                        log,
                        logging.WARNING,
                        "auth_anomaly",
                        event="auth_anomaly",
                        window_seconds=self.detector.window,
                        counts=counts,
                        threshold=self.detector.threshold,
                    )
