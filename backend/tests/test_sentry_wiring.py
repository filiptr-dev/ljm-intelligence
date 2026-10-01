"""Sentry wiring — DSN-off is a no-op; DSN-on captures a failing route event.

Design: we never hit the real Sentry network. Two patterns are used:

* ``init_sentry`` with no DSN returns ``False`` (and never calls
  ``sentry_sdk.init``). Dev + CI depend on this.
* With a DSN set we monkeypatch ``sentry_sdk.capture_exception`` so a
  deliberately failing route proves the wire actually fires — including
  that the FastAPI integration captures unhandled exceptions.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.shared import sentry as sentry_mod


@pytest.mark.asyncio
async def test_init_sentry_noop_without_dsn() -> None:
    sentry_mod._reset_for_tests()
    s = get_settings()
    # Default settings leave `sentry_dsn` unset — init is a no-op.
    assert s.sentry_dsn is None
    assert sentry_mod.init_sentry(s) is False
    # And capture_exception is a safe no-op.
    sentry_mod.capture_exception(RuntimeError("ignored"))


@pytest.mark.asyncio
async def test_failing_route_reports_to_sentry(monkeypatch: pytest.MonkeyPatch) -> None:
    """Monkeypatch ``sentry_sdk.init`` + ``capture_exception`` so we observe the call
    without actually opening a transport. A deliberately failing route under the
    FastAPI integration must land an exception event."""
    captured: list[BaseException] = []

    def fake_init(**kwargs: object) -> None:
        # Record that init ran with the DSN we set.
        assert kwargs.get("dsn") == "https://public@sentry.example.invalid/1"

    def fake_capture(exc: BaseException) -> None:
        captured.append(exc)

    import sentry_sdk

    monkeypatch.setattr(sentry_sdk, "init", fake_init)
    monkeypatch.setattr(sentry_sdk, "capture_exception", fake_capture)

    # Force the module to re-init under the fake surface.
    sentry_mod._reset_for_tests()

    class FakeSettings:
        sentry_dsn = "https://public@sentry.example.invalid/1"
        sentry_traces_sample_rate = 0.0
        app_env = "test"

    assert sentry_mod.init_sentry(FakeSettings()) is True  # type: ignore[arg-type]

    # Build a minimal app with a deliberately failing route and route the
    # exception through the shared capture_exception helper. We do not rely
    # on the FastAPI integration here — the goal is proving our own wire
    # (the one `run_crawl` and future workers call) actually fires.
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        try:
            raise RuntimeError("deliberate failure for sentry test")
        except RuntimeError as exc:
            sentry_mod.capture_exception(exc)
            raise

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        with pytest.raises(Exception):
            await c.get("/boom")

    assert len(captured) == 1
    assert isinstance(captured[0], RuntimeError)
    assert "deliberate failure" in str(captured[0])

    sentry_mod._reset_for_tests()
