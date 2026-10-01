"""Tiny async token-bucket rate limiter — one per mailbox + a global cap.

Not a dependency; just enough rope so a backfill does not get us throttled.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate_per_s: float
    capacity: float = 1.0
    _tokens: float = field(default=0.0, init=False)
    _last: float = field(default_factory=time.monotonic, init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity

    async def take(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate_per_s)
                self._last = now
                if self._tokens >= 1.0:
                    self._tokens -= 1.0
                    return
                need = (1.0 - self._tokens) / max(self.rate_per_s, 0.001)
                await asyncio.sleep(need)


class MailboxLimiter:
    """One bucket per mailbox, plus a shared global cap."""

    def __init__(self, per_mailbox_rps: float, global_rps: float) -> None:
        self._per_mailbox_rps = per_mailbox_rps
        self._global = TokenBucket(global_rps, capacity=max(1.0, global_rps))
        self._buckets: dict[str, TokenBucket] = {}

    async def acquire(self, mailbox: str) -> None:
        bucket = self._buckets.get(mailbox)
        if bucket is None:
            bucket = TokenBucket(self._per_mailbox_rps, capacity=max(1.0, self._per_mailbox_rps))
            self._buckets[mailbox] = bucket
        await bucket.take()
        await self._global.take()
