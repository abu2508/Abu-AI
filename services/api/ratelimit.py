"""In-process sliding-window rate limiter (no Redis by design).

Limits are per API process. That is acceptable for the single-worker VPS deployment; if the API
is ever scaled to several workers the effective limit multiplies, and a shared store is needed.
"""

import time
from collections import defaultdict, deque
from collections.abc import Callable


class RateLimiter:
    def __init__(self, clock: Callable[[], float] = time.monotonic, max_keys: int = 10_000) -> None:
        self._clock = clock
        self._hits: dict[str, deque[float]] = defaultdict(deque)
        self._max_keys = max_keys

    def check(self, key: str, limit: int, window_seconds: float = 60.0) -> float | None:
        """Record a hit. Returns None if allowed, else seconds until a slot frees up."""
        now = self._clock()
        if len(self._hits) > self._max_keys:
            self._prune(now, window_seconds)
        hits = self._hits[key]
        while hits and now - hits[0] >= window_seconds:
            hits.popleft()
        if len(hits) >= limit:
            return max(0.0, window_seconds - (now - hits[0]))
        hits.append(now)
        return None

    def _prune(self, now: float, window: float) -> None:
        for k in [k for k, h in self._hits.items() if not h or now - h[-1] >= window]:
            del self._hits[k]
