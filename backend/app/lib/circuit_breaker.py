"""Per-adapter circuit breaker — closes on 5xx / network, reopens after cooldown.

Placed in ``app/lib/`` because this class has zero load-board concepts: the
next connector (Gmail inbox / inbox-analysis) imports it unchanged. One
instance per vendor adapter — a DAT outage must not disable CHR.

Rules:

* ``record_failure(status)`` counts only 5xx and network errors (``status is
  None``). A 4xx (unauthorized, bad request) is not a vendor-health signal and
  resets the counter.
* Once ``_consecutive_failures >= threshold``, the breaker is open and
  ``is_open()`` returns True until ``cooldown_seconds`` elapse (monotonic
  clock, injectable for tests).
* After cooldown, the next call passes through. A success there resets fully;
  a failure re-opens with a fresh timer.
* ``is_open()`` short-circuits ``fetch()`` → return ``[]``, never raises. The
  adapter surfaces ``reason="circuit_open"`` so the Settings card can translate
  it to a plain sentence.
"""

from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class CircuitBreaker:
    threshold: int = 10
    cooldown_seconds: float = 15 * 60
    _consecutive_failures: int = 0
    _opened_at: float | None = None

    def is_open(self, now: float | None = None) -> bool:
        if self._opened_at is None:
            return False
        current = time.monotonic() if now is None else now
        # Cooldown elapsed → half-open: let the next call through. The breaker
        # is only truly closed again on ``record_success``.
        return current - self._opened_at < self.cooldown_seconds

    def record_success(self) -> None:
        self._consecutive_failures = 0
        self._opened_at = None

    def record_failure(self, status_code: int | None = None, now: float | None = None) -> None:
        # 4xx is not a vendor-health signal — reset.
        if status_code is not None and 400 <= status_code < 500:
            self._consecutive_failures = 0
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.threshold:
            self._opened_at = time.monotonic() if now is None else now


__all__ = ["CircuitBreaker"]
