"""Pure-ASGI middleware: request-size limit, IP rate limit, response hardening headers."""

import json
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from services.api.ratelimit import RateLimiter

STRICT_PATHS = ("/v1/auth/enroll", "/v1/auth/refresh")


def client_ip(scope: Scope, trusted_hops: int) -> str:
    """Socket peer by default. X-Forwarded-For is believed only for N trusted proxies, and only
    the hop appended by the closest trusted proxy is used (never the client-controlled left side)."""
    peer = (scope.get("client") or ("unknown", 0))[0]
    if trusted_hops <= 0:
        return str(peer)
    for name, value in scope.get("headers", []):
        if name == b"x-forwarded-for":
            parts = [p.strip() for p in value.decode("latin-1").split(",") if p.strip()]
            if len(parts) >= trusted_hops:
                return str(parts[-trusted_hops])
    return str(peer)


async def _json_response(
    send: Send, status: int, body: dict[str, Any], extra: list[tuple[bytes, bytes]]
) -> None:
    payload = json.dumps(body).encode()
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(payload)).encode()),
                *extra,
            ],
        }
    )
    await send({"type": "http.response.body", "body": payload})


class BodySizeLimitMiddleware:
    """Rejects bodies over max_bytes: by Content-Length up front, and while streaming."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app, self.max = app, max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope["headers"]:
            if name == b"content-length":
                try:
                    if int(value) > self.max:
                        await _json_response(send, 413, {"detail": "request too large"}, [])
                        return
                except ValueError:
                    await _json_response(send, 400, {"detail": "bad content-length"}, [])
                    return
        seen = 0
        started = False
        rejected = False

        async def limited_receive() -> Message:
            nonlocal seen, rejected
            msg = await receive()
            if msg["type"] == "http.request" and not rejected:
                seen += len(msg.get("body", b""))
                if seen > self.max:
                    # Answer 413 right here: raising would be rewritten into a 400 by the framework.
                    rejected = True
                    if not started:
                        await _json_response(send, 413, {"detail": "request too large"}, [])
                    return {"type": "http.disconnect"}
            return msg

        async def tracking_send(msg: Message) -> None:
            nonlocal started
            if rejected:
                return  # response already sent; swallow whatever the app tries to say next
            if msg["type"] == "http.response.start":
                started = True
            await send(msg)

        await self.app(scope, limited_receive, tracking_send)


class RateLimitMiddleware:
    """Per-IP limits: a global one, and a much stricter one for the unauthenticated auth endpoints."""

    def __init__(
        self,
        app: ASGIApp,
        limiter: RateLimiter,
        trusted_hops: int,
        per_minute: int,
        auth_per_minute: int,
    ) -> None:
        self.app, self.limiter, self.hops = app, limiter, trusted_hops
        self.per_minute, self.auth_per_minute = per_minute, auth_per_minute

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        ip = client_ip(scope, self.hops)
        path = scope["path"]
        checks = [(f"ip:{ip}", self.per_minute)]
        if path.startswith(STRICT_PATHS):
            checks.append((f"auth-ip:{ip}", self.auth_per_minute))
        for key, limit in checks:
            wait = self.limiter.check(key, limit)
            if wait is not None:
                await _json_response(
                    send,
                    429,
                    {"detail": "rate limit exceeded"},
                    [(b"retry-after", str(int(wait) + 1).encode())],
                )
                return
        await self.app(scope, receive, send)


class HardeningHeadersMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(msg: Message) -> None:
            if msg["type"] == "http.response.start":
                headers = [
                    (k, v)
                    for k, v in msg.get("headers", [])
                    if k not in (b"server", b"cache-control")
                ]
                headers += [
                    (b"cache-control", b"no-store"),
                    (b"x-content-type-options", b"nosniff"),
                ]
                msg = {**msg, "headers": headers}
            await send(msg)

        await self.app(scope, receive, send_with_headers)
