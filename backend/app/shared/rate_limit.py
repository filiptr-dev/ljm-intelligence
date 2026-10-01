"""Tiny in-memory sliding-window rate limiter.

Scope: single-process. On Render free we run one API dyno, which is enough
for Step 0's `/auth/login` guard (brute-force deterrent). A multi-process
deployment should replace this with a Postgres-backed sliding window
(same table that will host `jobs_health`), but that is out of scope for v1.

The limiter is deliberately not a FastAPI dependency on purpose — the
`/auth/login` handler calls it directly so we can key on BOTH the client IP
and the submitted email, which a dep would only see one of.

Returns the time (seconds) until the limit resets; the caller translates to
a 429 with `Retry-After`.
"""

from __future__ import annotations

import threading
import time
from collections import defaultdict, deque


class SlidingWindow:
    """Multi-key sliding-window counter. Not for high throughput."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._events: dict[tuple[str, str], deque[float]] = defaultdict(deque)

    def hit(self, bucket: str, key: str, *, limit: int, window_s: float) -> float:
        """Record a hit. Returns 0.0 if under limit, else seconds until reset."""
        now = time.monotonic()
        k = (bucket, key)
        with self._lock:
            q = self._events[k]
            cutoff = now - window_s
            while q and q[0] < cutoff:
                q.popleft()
            if len(q) >= limit:
                return max(0.1, q[0] + window_s - now)
            q.append(now)
            return 0.0

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


# One process-wide instance so routes just import + call.
LOGIN_LIMITER = SlidingWindow()


def check_login_rate(ip: str, email: str) -> float:
    """S0.5 — 5 attempts / minute / IP + 10 attempts / hour / email."""
    retry = LOGIN_LIMITER.hit("ip", ip, limit=5, window_s=60.0)
    if retry > 0:
        return retry
    return LOGIN_LIMITER.hit("email", email.lower().strip(), limit=10, window_s=3600.0)
