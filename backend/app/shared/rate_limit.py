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


# -- Trusted-proxy IP resolution (MF2) -------------------------------------
# Two paths:
#   1. BFF path: the Vercel Next route forwards the real browser IP in
#      ``X-LJM-Client-IP`` and authenticates itself with
#      ``X-LJM-Proxy-Secret: <shared secret>``. The backend trusts that
#      header ONLY when the secret matches ``settings.trusted_proxy_secret``.
#      Rationale: on a shared BFF every request to the backend arrives
#      from Vercel's IP pool; a 5/min per-IP limit on *that* IP would
#      lock every user out. The signed header is the escape hatch.
#   2. Direct path: parse ``X-Forwarded-For`` and take the N-th-from-right
#      entry, where N = ``trusted_proxy_hops``. The rightmost entries are
#      what trusted proxies appended; everything to the left of them is
#      attacker-controlled.
# Fallback: ``request.client.host`` (TCP peer).
import hmac as _hmac


def resolve_client_ip(
    *,
    xff_header: str | None,
    client_host: str | None,
    client_ip_header: str | None,
    proxy_secret_header: str | None,
    trusted_proxy_secret: str | None,
    trusted_proxy_hops: int,
) -> str:
    """Pure function — pick the IP the login rate limiter should key on.

    See ``app.api.auth.login`` for the FastAPI wiring that assembles the
    inputs from a ``Request``. Keeping the logic pure makes the spoofed-XFF
    + shared-proxy cases trivially testable.
    """
    # BFF path: trusted proxy identified itself via a shared secret.
    if (
        trusted_proxy_secret
        and proxy_secret_header
        and client_ip_header
        and _hmac.compare_digest(proxy_secret_header, trusted_proxy_secret)
    ):
        v = client_ip_header.strip()
        if v:
            return v

    # Direct path: parse XFF, take the N-th-from-right entry. If fewer
    # entries than hops, we got here through a path we don't trust —
    # fall back to the TCP peer (``request.client.host``).
    if xff_header:
        parts = [p.strip() for p in xff_header.split(",") if p.strip()]
        if len(parts) >= trusted_proxy_hops:
            picked = parts[-trusted_proxy_hops]
            if picked:
                return picked

    return client_host or "unknown"
