"""Crawl API — POST /crawl/run + three GETs over `crawl_runs`.

Reads delegate to ``app.integrations.crawl_service``. Starting a run stays
here because it owns FastAPI's ``BackgroundTasks`` + the intake-row-per-trigger
invariant.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.config import Settings
from app.integrations.crawl_service import (
    get_run as svc_get_run,
    latest_run as svc_latest_run,
    list_runs as svc_list_runs,
)
from app.models import CrawlRun
from app.pipeline.run import _new_run_id, run_crawl

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


from app.api._auth import check_secret as _check_secret  # re-export for backward compat


@router.post("/run", status_code=202)
async def start_run(
    request: Request,
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
    trigger: str = "on_demand",
    limit: int | None = Query(default=None, ge=1, le=50_000),
) -> dict:
    """Kick off a crawl. Returns 202 immediately; the pipeline runs in a background task.

    Single intake row per trigger — `run_crawl` reuses this id so `/crawl/runs` doesn't get
    two rows (one "wrapper", one "real") like the earlier version produced.

    ``limit`` (optional) caps the FMCSA rows fetched by this run. Omitted = the
    paginator's own page caps and time budget, same as the daily cron.
    """
    settings: Settings = request.app.state.settings
    _check_secret(settings, x_cron_secret)

    sessionmaker = request.app.state.sessionmaker
    trig = "cron" if trigger == "cron" else "on_demand"

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

    # When the queue is installed (Postgres + 0017), hand the pipeline to
    # the worker via `prospecting.crawl_leads` and return the job id so
    # the frontend can poll `/jobs/{id}`. Otherwise fall back to the
    # historical BackgroundTasks path (sqlite dev + whole test suite).
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        sessionmaker,
        "prospecting.crawl_leads",
        tenant_id=LJM_TENANT_ID,
        trigger=trig,
        limit=limit,
        run_id=intake_id,
    )
    if job_id is not None:
        # MF1 — bounded in-process drain so the fresh job starts in seconds,
        # not minutes (the GH-Actions cron fires every 5 min). The advisory
        # lock on hashtext('jobs.drain') serialises this with any concurrent
        # cron tick; the loser no-ops.
        from app.api.jobs import kick_in_process_drain
        background.add_task(
            kick_in_process_drain, sessionmaker, settings,
            seconds=settings.jobs_in_process_kick_seconds,
        )
        return {"run_id": intake_id, "status": "queued", "job_id": job_id}

    async def _work() -> None:
        await run_crawl(sessionmaker, settings, trigger=trig, fmcsa_limit=limit, run_id=intake_id)

    background.add_task(_work)
    return {"run_id": intake_id, "status": "queued"}


@router.get("/runs/{run_id}", response_model=CrawlRunOut)
async def show_run(request: Request, run_id: str) -> CrawlRunOut:
    row = await svc_get_run(request.app.state.sessionmaker, run_id)
    if row is None:
        raise HTTPException(404, "run not found")
    return CrawlRunOut(**row.__dict__)


@router.get("/runs", response_model=list[CrawlRunOut])
async def list_runs(request: Request, limit: int = 20) -> list[CrawlRunOut]:
    rows = await svc_list_runs(request.app.state.sessionmaker, limit=limit)
    return [CrawlRunOut(**row.__dict__) for row in rows]


@router.get("/latest", response_model=CrawlRunOut | None)
async def latest_run(request: Request) -> CrawlRunOut | None:
    row = await svc_latest_run(request.app.state.sessionmaker)
    return CrawlRunOut(**row.__dict__) if row else None
