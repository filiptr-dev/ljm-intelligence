"""Top-bar pill — ``last_crawl_at`` / ``last_crawl_status`` matrix.

Pins all five states from the plan (2026-10-06):
  * ``none``         — no CrawlRun rows for the tenant
  * ``running``      — row with status=='running' and started_at within 30 min
  * ``idle_recent``  — latest done finished <= 2h
  * ``idle_stale``   — latest done finished > 2h and <= 48h
  * ``error``        — latest terminal is error

Also pins tenant isolation on the pill (an other-tenant running row must
not flip this tenant's pill).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import kpi_service as kpi
from app.db import Base
from app.identity.models import Organization
from app.models import CrawlRun
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId, set_tenant

TENANT = TenantId(LJM_TENANT_ID)
OTHER = TenantId("01OTHERTENANT000000000000B")


@asynccontextmanager
async def _sessionmaker():
    set_tenant(TENANT)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as s:
            s.add(Organization(id=OTHER, slug="other-test", name="Other Co"))
            try:
                await s.commit()
            except Exception:  # noqa: BLE001 — swallow dup-insert from reuse
                await s.rollback()
        yield sm
    finally:
        await engine.dispose()


def _run(rid: str, tenant: str, *, status: str, started_at: datetime, finished_at: datetime | None) -> CrawlRun:
    return CrawlRun(
        id=rid, tenant_id=tenant, status=status, kind="main", trigger="on_demand",
        started_at=started_at, finished_at=finished_at,
    )


@pytest.mark.asyncio
async def test_none_when_no_rows() -> None:
    async with _sessionmaker() as sm, sm() as s:
        out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "none"
    assert out["last_crawl_at"] is None


@pytest.mark.asyncio
async def test_running_within_30_min() -> None:
    now = datetime.now(UTC)
    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add(_run("r1", TENANT, status="running", started_at=now - timedelta(minutes=5), finished_at=None))
            await s.commit()
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "running"
    assert out["last_crawl_at"] is not None


@pytest.mark.asyncio
async def test_idle_recent_within_two_hours() -> None:
    now = datetime.now(UTC)
    finished = now - timedelta(hours=1)
    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add(_run("r1", TENANT, status="done", started_at=finished - timedelta(minutes=10), finished_at=finished))
            await s.commit()
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "idle_recent"
    assert out["last_crawl_at"] is not None


@pytest.mark.asyncio
async def test_idle_stale_beyond_two_hours() -> None:
    now = datetime.now(UTC)
    finished = now - timedelta(hours=8)
    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add(_run("r1", TENANT, status="done", started_at=finished - timedelta(minutes=10), finished_at=finished))
            await s.commit()
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "idle_stale"


@pytest.mark.asyncio
async def test_error_when_latest_terminal_errored() -> None:
    now = datetime.now(UTC)
    finished = now - timedelta(hours=1)
    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add(_run("r1", TENANT, status="error", started_at=finished - timedelta(minutes=10), finished_at=finished))
            await s.commit()
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "error"


@pytest.mark.asyncio
async def test_tenant_isolation_on_pill() -> None:
    now = datetime.now(UTC)
    async with _sessionmaker() as sm:
        async with sm() as s:
            # Other tenant is actively running; this tenant has nothing.
            s.add(_run("r-other", OTHER, status="running", started_at=now - timedelta(minutes=1), finished_at=None))
            await s.commit()
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["last_crawl_status"] == "none"
    assert out["last_crawl_at"] is None
