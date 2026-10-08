"""Drain overlap + stuck jobs (task 2026-10-05 drain-overlap-stuck-jobs).

Observed on a local PG demo DB: a ``POST /jobs/drain`` overlapping the
in-process drain kick returned 500 ``AppNotOpen`` and left jobs in ``doing``.
Root cause: the advisory lock was taken on a pooled connection that went
straight back to the pool (advisory locks are re-entrant per connection), so
both drains ran, and the first to finish closed the shared procrastinate app
under the other.

sqlite tests (always run): crawl-run terminal status helpers.
PG tests (TEST_HARNESS=pg16): overlap never raises, second caller skips,
stalled ``doing`` jobs are failed and their crawl run closed.
"""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.models import CrawlRun
from app.pipeline.run import abort_crawl_run
from app.shared.orm import LJM_TENANT_ID

_PG = os.environ.get("TEST_HARNESS", "").lower() == "pg16"
pg_only = pytest.mark.skipif(not _PG, reason="drain tests require TEST_HARNESS=pg16")


def _run(rid: str, status: str) -> CrawlRun:
    return CrawlRun(
        id=rid,
        started_at=datetime.now(UTC),
        status=status,
        kind="fmcsa+gemini",
        counts={},
        trigger="on_demand",
        tenant_id=LJM_TENANT_ID,
    )


@pytest.fixture
async def sqlite_sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


# ---------- sqlite: terminal status for crawl runs ---------------------------


@pytest.mark.skipif(_PG, reason="sqlite-only (PG RLS path covered by the stalled-job test)")
@pytest.mark.asyncio
async def test_abort_crawl_run_closes_open_runs_only(sqlite_sm) -> None:
    async with sqlite_sm() as s:
        s.add_all([_run("run_open", "running"), _run("run_queued", "queued"), _run("run_done", "done")])
        await s.commit()

    for rid in ("run_open", "run_queued", "run_done"):
        await abort_crawl_run(sqlite_sm, rid, tenant_id=None, error="boom")

    async with sqlite_sm() as s:
        rows = {r.id: r for r in (await s.execute(select(CrawlRun))).scalars()}
    assert rows["run_open"].status == "error" and rows["run_open"].finished_at is not None
    assert rows["run_queued"].status == "error"
    assert rows["run_done"].status == "done" and rows["run_done"].error is None


@pytest.mark.asyncio
async def test_crawl_job_failure_closes_its_run() -> None:
    """Anything escaping run_crawl inside the job must close the run, then re-raise."""
    from app.prospecting import jobs as pjobs

    boom = RuntimeError("db went away on the final write")
    with (
        patch("app.pipeline.run.run_crawl", AsyncMock(side_effect=boom)),
        patch("app.pipeline.run.abort_crawl_run", AsyncMock()) as abort,
        pytest.raises(RuntimeError),
    ):
        await pjobs.crawl_leads.func(tenant_id=LJM_TENANT_ID, trigger="on_demand", run_id="run_x")
    abort.assert_awaited_once()
    assert abort.await_args.args[1] == "run_x"
    assert abort.await_args.kwargs["tenant_id"] == LJM_TENANT_ID


# ---------- PG: drain overlap + stalled recovery -----------------------------


@pytest.fixture
async def pg_sm():
    engine = create_async_engine(os.environ["DATABASE_URL_TEST_PG"], future=True)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        await s.execute(text("DELETE FROM procrastinate_jobs"))
        await s.commit()
    yield sm
    await engine.dispose()


@pg_only
@pytest.mark.asyncio
async def test_overlapping_drains_never_raise_and_second_skips(pg_sm) -> None:
    from app.integrations.jobs_router import _drain_once
    from app.shared.queue import app as queue_app, dispatch

    if "tests.slow_noop" not in queue_app.tasks:

        @queue_app.task(name="tests.slow_noop", queue="default", pass_context=False)
        async def _slow_noop(tenant_id: str) -> None:
            await asyncio.sleep(1.5)

    jid = await dispatch("tests.slow_noop", tenant_id=LJM_TENANT_ID)

    first, second = await asyncio.gather(
        _drain_once(pg_sm, seconds=5),
        _drain_once(pg_sm, seconds=5),
    )
    outs = sorted([first, second], key=lambda o: o.skipped_overlap)
    assert outs[0].skipped_overlap is False and outs[0].succeeded >= 1
    assert outs[1].skipped_overlap is True

    async with pg_sm() as s:
        status = (await s.execute(text("SELECT status FROM procrastinate_jobs WHERE id=:i"), {"i": jid})).scalar_one()
    assert status == "succeeded"

    # The shared app is usable again afterwards (nobody left it closed mid-use).
    again = await _drain_once(pg_sm, seconds=2)
    assert again.skipped_overlap is False


@pg_only
@pytest.mark.asyncio
async def test_drain_skips_when_another_process_holds_the_lock(pg_sm) -> None:
    from app.integrations.jobs_router import _drain_once

    async with pg_sm() as other:
        await other.execute(text("SELECT pg_advisory_xact_lock(hashtext('jobs.drain'))"))
        out = await _drain_once(pg_sm, seconds=2)
        assert out.skipped_overlap is True
    # lock released with the other transaction → next drain runs
    assert (await _drain_once(pg_sm, seconds=2)).skipped_overlap is False


@pg_only
@pytest.mark.asyncio
async def test_stalled_doing_job_is_failed_and_crawl_run_closed(pg_sm) -> None:
    from app.integrations.jobs_router import _drain_once
    from app.shared.queue import dispatch

    async with pg_sm() as s:
        await s.execute(text("DELETE FROM crawl_runs WHERE id = 'run_stalled'"))
        s.add(_run("run_stalled", "running"))
        await s.commit()

    jid = await dispatch("prospecting.crawl_leads", tenant_id=LJM_TENANT_ID, trigger="on_demand", run_id="run_stalled")
    # Simulate the observed state: picked up by a worker that is gone.
    async with pg_sm() as s:
        await s.execute(text("UPDATE procrastinate_jobs SET status='doing', worker_id=NULL WHERE id=:i"), {"i": jid})
        await s.commit()

    out = await _drain_once(pg_sm, seconds=2)
    assert out.skipped_overlap is False

    async with pg_sm() as s:
        status = (await s.execute(text("SELECT status FROM procrastinate_jobs WHERE id=:i"), {"i": jid})).scalar_one()
        run = (await s.execute(select(CrawlRun).where(CrawlRun.id == "run_stalled"))).scalar_one()
    assert status == "failed"
    assert run.status == "error" and "stalled" in (run.error or "")
