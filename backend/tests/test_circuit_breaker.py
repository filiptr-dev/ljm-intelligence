"""CircuitBreaker — tests for closed → open on 5xx + reopen after cooldown.

Clock is injected via the ``now`` keyword on ``is_open`` / ``record_failure``
so no monkeypatching of ``time.monotonic`` is needed; the breaker's contract
is "whatever clock you pass me, I'll use it", and the adapter default
(``now=None`` → ``time.monotonic()``) is the production path.
"""

from __future__ import annotations

import httpx
import pytest
from pydantic import SecretStr

from app.config import Settings
from app.lib.circuit_breaker import CircuitBreaker
from app.integrations.adapters.loadboard.chr import ChrSource
from app.integrations.adapters.loadboard.dat import DatSource
from app.integrations.adapters.loadboard.loadboard123 import LoadBoard123Source
from app.integrations.adapters.loadboard.truckstop import TruckstopSource


def test_fresh_breaker_is_closed() -> None:
    cb = CircuitBreaker()
    assert cb.is_open() is False


def test_nine_5xx_stays_closed_tenth_opens() -> None:
    cb = CircuitBreaker()
    for _ in range(9):
        cb.record_failure(500, now=0.0)
    assert cb.is_open(now=0.0) is False
    cb.record_failure(500, now=0.0)
    assert cb.is_open(now=0.0) is True


def test_4xx_resets_counter() -> None:
    cb = CircuitBreaker()
    for _ in range(5):
        cb.record_failure(500, now=0.0)
    cb.record_failure(401, now=0.0)  # 4xx — not vendor health
    # Counter was reset; need another ten to open.
    for _ in range(9):
        cb.record_failure(500, now=0.0)
    assert cb.is_open(now=0.0) is False
    cb.record_failure(500, now=0.0)
    assert cb.is_open(now=0.0) is True


def test_success_resets_counter() -> None:
    cb = CircuitBreaker()
    for _ in range(5):
        cb.record_failure(500, now=0.0)
    cb.record_success()
    assert cb.is_open(now=0.0) is False
    # Nine more 5xx should still keep it closed (counter was zeroed).
    for _ in range(9):
        cb.record_failure(500, now=0.0)
    assert cb.is_open(now=0.0) is False


def test_network_errors_open_the_breaker() -> None:
    cb = CircuitBreaker()
    for _ in range(10):
        cb.record_failure(None, now=0.0)
    assert cb.is_open(now=0.0) is True


def test_is_open_true_immediately_after_tenth_failure() -> None:
    cb = CircuitBreaker()
    for _ in range(10):
        cb.record_failure(500, now=0.0)
    # Same instant — the breaker should already be open.
    assert cb.is_open(now=0.0) is True


def test_is_open_false_after_cooldown_elapses() -> None:
    cb = CircuitBreaker(cooldown_seconds=60.0)
    for _ in range(10):
        cb.record_failure(500, now=0.0)
    assert cb.is_open(now=59.9) is True
    assert cb.is_open(now=60.0) is False


def test_after_cooldown_probe_success_stays_closed() -> None:
    cb = CircuitBreaker(cooldown_seconds=60.0)
    for _ in range(10):
        cb.record_failure(500, now=0.0)
    # Cooldown elapsed, probe succeeds.
    assert cb.is_open(now=61.0) is False
    cb.record_success()
    # Still closed, counter at zero — nine new 5xx shouldn't trip it.
    for _ in range(9):
        cb.record_failure(500, now=62.0)
    assert cb.is_open(now=62.0) is False


def test_after_cooldown_probe_failure_reopens_with_fresh_timer() -> None:
    cb = CircuitBreaker(cooldown_seconds=60.0, threshold=10)
    for _ in range(10):
        cb.record_failure(500, now=0.0)
    assert cb.is_open(now=61.0) is False  # cooldown elapsed
    # The probe fails — this is the half-open attempt. The breaker is
    # already at ``threshold`` consecutive failures, so one more re-opens
    # with a fresh ``_opened_at`` timer.
    cb.record_failure(500, now=61.0)
    assert cb.is_open(now=61.0) is True
    # Timer reset — still open at the old deadline, closed at new deadline.
    assert cb.is_open(now=120.9) is True
    assert cb.is_open(now=121.0) is False


def test_two_independent_breakers_do_not_share_state() -> None:
    a = CircuitBreaker()
    b = CircuitBreaker()
    for _ in range(10):
        a.record_failure(500, now=0.0)
    assert a.is_open(now=0.0) is True
    assert b.is_open(now=0.0) is False


# --- AC4: each vendor adapter honors the breaker ---------------------------
#
# After 10 consecutive 5xx failures, the breaker is open and ``fetch()`` must
# return ``[]`` without issuing an HTTP request. Tripwire: monkeypatch
# ``httpx.AsyncClient`` on the adapter's module so any attempt to construct a
# client raises — if the breaker short-circuit didn't fire, the test fails
# loudly with the AssertionError below.


def _enabled_settings() -> Settings:
    return Settings().model_copy(
        update={
            "dat_service_account_email": "sa@dat",
            "dat_service_account_password": SecretStr("pw"),
            "dat_org_id": "ORG",
            "chr_client_id": "cid",
            "chr_client_secret": SecretStr("cs"),
            "chr_carrier_code": "CODE",
            "lb123_api_key": SecretStr("k"),
            "lb123_carrier_username": "u",
            "lb123_carrier_password": SecretStr("p"),
            "truckstop_integration_id": SecretStr("iid"),
            "truckstop_username": "u",
            "truckstop_password": SecretStr("p"),
        }
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "cls, module_path",
    [
        (DatSource, "app.integrations.adapters.loadboard.dat"),
        (ChrSource, "app.integrations.adapters.loadboard.chr"),
        (LoadBoard123Source, "app.integrations.adapters.loadboard.loadboard123"),
        (TruckstopSource, "app.integrations.adapters.loadboard.truckstop"),
    ],
)
async def test_adapter_fetch_short_circuits_when_breaker_open(
    cls, module_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings = _enabled_settings()
    adapter = cls(settings)
    assert adapter.enabled is True
    # Simulate 10 consecutive 5xx failures.
    for _ in range(10):
        adapter._breaker.record_failure(500)
    assert adapter._breaker.is_open() is True

    # Tripwire: if fetch reaches httpx, this blows up with a clear error.
    def _tripwire(*_a, **_kw):
        raise AssertionError(f"{cls.__name__}.fetch issued an HTTP request while breaker was open")

    monkeypatch.setattr(f"{module_path}.httpx.AsyncClient", _tripwire)

    rows = await adapter.fetch(settings)
    assert rows == []
    # And the adapter's user-facing reason now carries ``circuit_open``.
    assert adapter.reason() == "circuit_open"
    # Keep the import referenced so ruff doesn't trim it.
    assert httpx is not None
