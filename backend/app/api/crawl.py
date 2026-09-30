"""Crawl API: POST /crawl/run + GET /crawl/runs (+ {id})."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.config import Settings
from app.models import CrawlRun
from app.pipeline.run import _new_run_id, get_run, run_crawl

log = logging.getLogger(__name__)

router = APIRouter(prefix="/crawl", tags=["crawl"])


class CrawlRunOut(BaseModel):
    id: str
    status: str
    kind: str
    trigger: str
    started_at: str
    finished_at: str | None
    counts: dict
    error: str | None = None


def _serialize(r: CrawlRun) -> CrawlRunOut:
    return CrawlRunOut(
        id=r.id,
        status=r.status,
        kind=r.kind,
        trigger=r.trigger,
        started_at=r.started_at.isoformat() if r.started_at else "",
        finished_at=r.finished_at.isoformat() if r.finished_at else None,
        counts=r.counts or {},
        error=r.error,
    )


from app.api._auth import check_secret as _check_secret  # re-export for backward compat


@router.post("/run", status_code=202)
async def start_run(
    request: Request,
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    trigger: str = "on_demand",
    limit: int = 500,
) -> dict:
    """Kick off a crawl. Returns 202 immediately; the pipeline runs in a background task.

    Single intake row per trigger — `run_crawl` reuses this id so `/crawl/runs` doesn't get
    two rows (one "wrapper", one "real") like the earlier version produced.
    """
    settings: Settings = request.app.state.settings
    _check_secret(settings, x_cron_secret)

    sessionmaker = request.app.state.sessionmaker
    trig = "cron" if trigger == "cron" else "on_demand"
    limit = max(1, min(limit, 2000))

    intake_id = _new_run_id()
    started = datetime.now(UTC)
    async with sessionmaker() as s:
        s.add(
            CrawlRun(
                id=intake_id,
                started_at=started,
                status="queued",
                kind="fmcsa+gemini",
                counts={"discovered": 0, "new": 0, "scored": 0, "auto_contacted": 0},
                trigger=trig,
            )
        )
        await s.commit()

    async def _work() -> None:
        await run_crawl(sessionmaker, settings, trigger=trig, fmcsa_limit=limit, run_id=intake_id)

    background.add_task(_work)
    return {"run_id": intake_id, "status": "queued"}


@router.get("/runs/{run_id}", response_model=CrawlRunOut)
async def show_run(request: Request, run_id: str) -> CrawlRunOut:
    r = await get_run(request.app.state.sessionmaker, run_id)
    if not r:
        raise HTTPException(404, "run not found")
    return _serialize(r)


@router.get("/runs", response_model=list[CrawlRunOut])
async def list_runs(request: Request, limit: int = 20) -> list[CrawlRunOut]:
    limit = max(1, min(limit, 100))
    async with request.app.state.sessionmaker() as s:
        res = await s.execute(select(CrawlRun).order_by(desc(CrawlRun.started_at)).limit(limit))
        return [_serialize(r) for r in res.scalars().all()]


@router.get("/latest", response_model=CrawlRunOut | None)
async def latest_run(request: Request) -> CrawlRunOut | None:
    async with request.app.state.sessionmaker() as s:
        res = await s.execute(select(CrawlRun).order_by(desc(CrawlRun.started_at)).limit(1))
        r = res.scalar_one_or_none()
    return _serialize(r) if r else None
