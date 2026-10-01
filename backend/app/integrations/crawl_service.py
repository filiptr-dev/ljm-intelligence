"""Integrations crawl service — DB-shaped reads over `crawl_runs`.

The thin ``app/api/crawl.py`` router delegates here for the three GET
endpoints. Starting a run stays in the router because it owns FastAPI's
``BackgroundTasks`` (and the per-trigger intake row) — scheduling is HTTP
plumbing, not domain work. The service's job is: given a session, return
the shape the UI needs.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.models import CrawlRun


@dataclass
class CrawlRunRow:
    id: str
    status: str
    kind: str
    trigger: str
    started_at: str
    finished_at: str | None
    counts: dict
    error: str | None = None


def _row(r: CrawlRun) -> CrawlRunRow:
    return CrawlRunRow(
        id=r.id,
        status=r.status,
        kind=r.kind,
        trigger=r.trigger,
        started_at=r.started_at.isoformat() if r.started_at else "",
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        counts=r.counts or {},
        error=r.error,
    )


async def get_run(
    sessionmaker: async_sessionmaker[AsyncSession], run_id: str
) -> CrawlRunRow | None:
    async with sessionmaker() as s:
        res = await s.execute(select(CrawlRun).where(CrawlRun.id == run_id))
        r = res.scalar_one_or_none()
    return _row(r) if r else None


async def list_runs(
    sessionmaker: async_sessionmaker[AsyncSession], limit: int = 20
) -> list[CrawlRunRow]:
    limit = max(1, min(limit, 100))
    async with sessionmaker() as s:
        res = await s.execute(
            select(CrawlRun).order_by(desc(CrawlRun.started_at)).limit(limit)
        )
        return [_row(r) for r in res.scalars().all()]


async def latest_run(
    sessionmaker: async_sessionmaker[AsyncSession],
) -> CrawlRunRow | None:
    async with sessionmaker() as s:
        res = await s.execute(
            select(CrawlRun).order_by(desc(CrawlRun.started_at)).limit(1)
        )
        r = res.scalar_one_or_none()
    return _row(r) if r else None
