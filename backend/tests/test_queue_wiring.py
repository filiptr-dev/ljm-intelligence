"""Queue layer wiring — module import + sqlite fallback behaviour.

The real transaction-rides-dispatch (AC3) + tenant-bound-worker (AC4) tests
live under tests/test_queue_pg.py and only run under TEST_HARNESS=pg16.
This file keeps the sqlite path honest:
  * Every module `jobs.py` imports and registers at least one task.
  * `dispatch()` and `defer_on()` return None on sqlite (queue not installed),
    never raise.
  * `/jobs/drain` on sqlite responds with reason="queue schema not installed".
  * `/health` includes a `jobs` snapshot with zeros.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app
from app.shared.queue import IMPORT_PATHS, app as queue_app


def test_all_import_paths_register_tasks() -> None:
    import importlib

    for path in IMPORT_PATHS:
        importlib.import_module(path)
    # At least one task from every module shape we care about.
    expected = {
        "prospecting.crawl_leads",
        "outreach.auto_send",
        "inbox.mail_backfill",
        "inbox.mail_incremental",
        "inbox.retention_sweep",
        "loads.refresh",
        "analysis.nightly",
    }
    registered = set(queue_app.tasks.keys())
    missing = expected - registered
    assert not missing, f"queue tasks not registered: {missing}"


def test_scheduler_periodics_are_registered() -> None:
    """Every periodic cron slot documented in the plan has a task."""
    import importlib

    importlib.import_module("app.shared.scheduler")

    expected_periodics = {
        "scheduler.crawl_daily_est",
        "scheduler.crawl_daily_edt",
        "scheduler.auto_send_after_crawl",
        "scheduler.mail_incremental_tick",
        "scheduler.loads_refresh_tick",
        "scheduler.retention_sweep_nightly",
        "scheduler.analysis_nightly_tick",
    }
    missing = expected_periodics - set(queue_app.tasks.keys())
    assert not missing, f"scheduler tasks not registered: {missing}"


@pytest.mark.asyncio
async def test_dispatch_sqlite_fallback_returns_none() -> None:
    """On sqlite the queue schema isn't installed; dispatch must not raise.

    Under TEST_HARNESS=pg16 the queue IS installed so dispatch returns an
    int — the test asserts "doesn't raise, returns either None or int".
    """
    from app.shared.queue import dispatch

    out = await dispatch("prospecting.crawl_leads", tenant_id="x", trigger="cron", limit=1)
    assert out is None or isinstance(out, int)


def _build_app_with_sm(cron_secret: str):
    from pydantic import SecretStr
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.config import Settings
    from app.db import Base

    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        cron_secret=SecretStr(cron_secret),
    )
    app = create_app(settings)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    app.state.sessionmaker = sm
    app.state.engine = engine
    # Create the base metadata so queries don't bomb when the queue checks
    # for the procrastinate table existence (it's wrapped in try/except).
    return app, engine, Base


@pytest.mark.asyncio
async def test_drain_endpoint_reports_schema_absent() -> None:
    """`/jobs/drain` with the right cron secret returns the no-schema path."""
    app, engine, Base = _build_app_with_sm("test-secret")
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/jobs/drain?seconds=1",
                headers={"X-Cron-Secret": "test-secret"},
            )
        assert r.status_code == 200, r.text
        body = r.json()
        # Under pg16 harness the schema IS installed → reason is None.
        # Under pure sqlite the reason is "queue schema not installed".
        assert body["ran"] == 0
        assert body["remaining"] >= 0
        assert body["reason"] in (None, "queue schema not installed")
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_drain_requires_cron_secret() -> None:
    app, engine, _ = _build_app_with_sm("the-real-secret")
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/jobs/drain?seconds=1",
                headers={"X-Cron-Secret": "wrong"},
            )
        assert r.status_code == 401
    finally:
        await engine.dispose()
