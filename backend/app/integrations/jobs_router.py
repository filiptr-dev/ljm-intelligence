"""Jobs API — the free-host drain endpoint + minimal admin surface.

* ``POST /jobs/drain?seconds=25`` — bounded worker tick, protected by
  CRON_SECRET. The GitHub Actions ``queue-drain.yml`` posts here every
  5 minutes; on the user's own infra the always-on worker makes this
  endpoint redundant (safe to keep wired).
* ``GET  /jobs/{id}`` — show one job row (status, attempts, last error).
* ``GET  /admin/jobs`` — owner-only list of queued/running/failed.
* ``POST /jobs/{id}/retry`` — owner-only one-click retry.

Overlap: an in-process ``asyncio.Lock`` plus a transaction-scoped advisory
lock on ``hashtext('jobs.drain')`` make a second concurrent caller return
``skipped_overlap`` instead of sharing (and closing) the procrastinate app.
Combined with ``SKIP LOCKED`` on the workers this is belt + braces.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from app.shared.cron_auth import check_secret
from app.config import Settings

log = logging.getLogger(__name__)

router = APIRouter(prefix="/jobs", tags=["jobs"])
admin_router = APIRouter(prefix="/admin", tags=["admin"])


class DrainOut(BaseModel):
    ran: int
    succeeded: int
    failed: int
    remaining: int
    skipped_overlap: bool = False
    reason: str | None = None


class JobOut(BaseModel):
    id: int
    task_name: str
    status: str
    queue_name: str
    attempts: int
    scheduled_at: str | None
    args: dict
    last_error: str | None = None


class JobsListOut(BaseModel):
    items: list[JobOut]


class RetryOut(BaseModel):
    ok: bool
    id: int


async def _queue_installed(sessionmaker) -> bool:
    """Does the current DB have procrastinate's schema?"""
    try:
        async with sessionmaker() as s:
            row = (
                await s.execute(
                    text(
                        "SELECT 1 FROM information_schema.tables "
                        "WHERE table_name = 'procrastinate_jobs' LIMIT 1"
                    )
                )
            ).first()
            return row is not None
    except Exception:  # noqa: BLE001
        return False


# In-process gate. The procrastinate ``App`` is a module-level singleton and
# ``open_async()`` is a no-op when already open while ``close_async()`` closes
# the pool for *every* user — so two drains in one process must never overlap,
# or the first to finish closes the app under the other (``AppNotOpen``: a 500
# on ``/jobs/drain`` and jobs left in ``doing`` because their final status can't
# be persisted). Checked-then-acquired with no ``await`` in between, so the
# check is atomic on the event loop.
_DRAIN_LOCK = asyncio.Lock()

# Cross-process gate (cron drain vs. a separate API / worker process). A
# *transaction*-scoped advisory lock held on one checked-out connection for the
# whole drain: it can't be re-entered by another drain that happens to reuse a
# pooled connection (the old session-level lock was taken on a connection that
# went straight back to the pool, and advisory locks are re-entrant per
# connection), it survives PgBouncer transaction mode, and it is released by
# the rollback when the session closes — no unlock on the wrong connection.
_TRY_LOCK_SQL = text("SELECT pg_try_advisory_xact_lock(hashtext('jobs.drain'))")


async def _skipped(sessionmaker) -> DrainOut:
    remaining = await _count_queued(sessionmaker)
    return DrainOut(
        ran=0, succeeded=0, failed=0, remaining=remaining,
        skipped_overlap=True, reason="another drain in progress",
    )


async def _drain_once(sessionmaker, *, seconds: int) -> DrainOut:
    """Bounded in-process drain. Reused by `/jobs/drain` AND by on-demand
    HTTP routes that need the worker to pick up their fresh job within
    seconds (not up to 5 min when waiting for the cron drain).

    Overlap contract: a second caller — same process or another one — gets
    ``skipped_overlap=True`` and never touches the shared procrastinate app.
    """
    if not await _queue_installed(sessionmaker):
        return DrainOut(ran=0, succeeded=0, failed=0, remaining=0, reason="queue schema not installed")

    if _DRAIN_LOCK.locked():
        return await _skipped(sessionmaker)

    async with _DRAIN_LOCK, sessionmaker() as lock_session:
        got_lock = bool((await lock_session.execute(_TRY_LOCK_SQL)).scalar())
        if not got_lock:
            return await _skipped(sessionmaker)
        # Lock is held until ``lock_session`` closes (rollback releases it).
        return await _drain_locked(sessionmaker, seconds=seconds)


async def _drain_locked(sessionmaker, *, seconds: int) -> DrainOut:
    from app.shared.queue import app as queue_app

    before = await _status_counts(sessionmaker)

    async with queue_app.open_async():
        # Jobs whose worker died (process killed, or an earlier overlap closed
        # the app under it) sit in ``doing`` forever. Fail them so they show up
        # in /admin/jobs with one-click retry, and close their crawl run.
        await _fail_stalled_jobs(queue_app, sessionmaker)

        # Enqueue any periodic jobs whose run_at has passed. On an
        # always-on worker this is automatic; on free we poke it here.
        try:
            await queue_app._register_builtin_tasks()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — older/newer procrastinate paths
            log.debug("_register_builtin_tasks skipped: %s", exc)

        try:
            # procrastinate shields its run loop from cancellation: on timeout
            # it stops fetching and waits for the in-flight job to finish, so a
            # bounded tick never tears a job in half.
            await asyncio.wait_for(
                queue_app.run_worker_async(
                    queues=["default"],
                    wait=False,
                    install_signal_handlers=False,
                    listen_notify=False,
                ),
                timeout=float(seconds),
            )
        except TimeoutError:
            pass  # expected — bounded tick ending

    after = await _status_counts(sessionmaker)
    succeeded = max(0, after.get("succeeded", 0) - before.get("succeeded", 0))
    failed = max(0, after.get("failed", 0) - before.get("failed", 0))
    return DrainOut(
        ran=succeeded + failed, succeeded=succeeded, failed=failed,
        remaining=after.get("todo", 0),
    )


async def _fail_stalled_jobs(queue_app, sessionmaker) -> int:
    """Mark ``doing`` jobs with a dead worker (no heartbeat for 30s) as failed.

    Heartbeat-based, so a job on a live always-on worker is never touched; the
    recovery matters for free-host / ephemeral drains whose worker vanished.

    Failed, not retried: a stalled job may already have sent mail or written
    half its rows, so re-running it is an operator decision (``/jobs/{id}/retry``).
    A stalled ``prospecting.crawl_leads`` also gets its ``crawl_runs`` row moved
    to ``error`` so Overview stops waiting on a run that will never finish.
    """
    from procrastinate.jobs import Status

    from app.pipeline.run import abort_crawl_run

    try:
        stalled = list(await queue_app.job_manager.get_stalled_jobs())
    except Exception as exc:  # noqa: BLE001 — recovery must never block the drain
        log.warning("stalled-job scan failed: %s", exc)
        return 0
    for job in stalled:
        try:
            await queue_app.job_manager.finish_job(job, status=Status.FAILED, delete_job=False)
            kwargs = job.task_kwargs or {}
            if job.task_name == "prospecting.crawl_leads" and kwargs.get("run_id"):
                await abort_crawl_run(
                    sessionmaker,
                    str(kwargs["run_id"]),
                    tenant_id=kwargs.get("tenant_id"),
                    error="worker stalled before the crawl finished (job failed by drain recovery)",
                )
            log.warning("failed stalled job %s (%s)", job.id, job.task_name)
        except Exception as exc:  # noqa: BLE001
            log.warning("could not fail stalled job %s: %s", job.id, exc)
    return len(stalled)


async def kick_in_process_drain(sessionmaker, settings, *, seconds: int = 10) -> None:
    """Fire-and-forget drain used by on-demand routes.

    Guarded by ``settings.jobs_in_process_kick_enabled`` — on the user's
    own infra where an always-on worker container exists, this is set to
    False so the API process doesn't duplicate the worker's work. On
    Render free (no worker service, cron every 5 min) it's True by
    default so "Crawl now" doesn't wait minutes.

    Catches every exception — the HTTP response has already been sent;
    a failure to drain is logged and the next cron tick picks the job up
    anyway. We never re-raise into the background task scheduler.
    """
    if not getattr(settings, "jobs_in_process_kick_enabled", True):
        return
    try:
        await _drain_once(sessionmaker, seconds=seconds)
    except Exception as exc:  # noqa: BLE001
        log.warning("kick_in_process_drain failed (next cron tick will retry): %s", exc)


@router.post("/drain", response_model=DrainOut)
async def drain(
    request: Request,
    seconds: int = Query(default=25, ge=1, le=55),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> DrainOut:
    """Run the worker for ``seconds`` and return a summary.

    Protected by ``CRON_SECRET`` and only callable by the GitHub Actions
    ``queue-drain.yml`` workflow. The router-level ``require_user_or_cron``
    dep accepts a bearer token too, but the per-handler ``check_secret``
    call fences the actual work: a user with only a bearer gets 401 here.
    """
    settings: Settings = request.app.state.settings
    # The cron secret is the authoritative guard — a bearer without it
    # cannot drain. (The router dep is there for routing-table symmetry.)
    check_secret(settings, x_cron_secret)

    return await _drain_once(request.app.state.sessionmaker, seconds=seconds)


async def _status_counts(sessionmaker) -> dict[str, int]:
    try:
        async with sessionmaker() as s:
            rows = (
                await s.execute(
                    text("SELECT status, COUNT(*) FROM procrastinate_jobs GROUP BY status")
                )
            ).all()
            return {str(r[0]): int(r[1]) for r in rows}
    except Exception:  # noqa: BLE001
        return {}


async def _count_queued(sessionmaker) -> int:
    try:
        async with sessionmaker() as s:
            row = (
                await s.execute(
                    text("SELECT COUNT(*) FROM procrastinate_jobs WHERE status = 'todo'")
                )
            ).first()
            return int(row[0]) if row else 0
    except Exception:  # noqa: BLE001
        return 0


@router.get("/{job_id}", response_model=JobOut)
async def get_job(job_id: int, request: Request) -> JobOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        row = (
            await s.execute(
                text(
                    "SELECT id, task_name, status, queue_name, attempts, scheduled_at, args "
                    "FROM procrastinate_jobs WHERE id = :id"
                ),
                {"id": job_id},
            )
        ).first()
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="job not found")
        err_row = (
            await s.execute(
                text(
                    "SELECT type FROM procrastinate_events WHERE job_id = :id "
                    "ORDER BY at DESC LIMIT 1"
                ),
                {"id": job_id},
            )
        ).first()
    return JobOut(
        id=int(row[0]), task_name=str(row[1]), status=str(row[2]),
        queue_name=str(row[3]), attempts=int(row[4]),
        scheduled_at=row[5].isoformat() if row[5] else None,
        args=row[6] or {},
        last_error=str(err_row[0]) if err_row else None,
    )


@admin_router.get("/jobs", response_model=JobsListOut)
async def list_jobs(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
) -> JobsListOut:
    sessionmaker = request.app.state.sessionmaker
    where = ""
    params: dict = {"limit": limit}
    if status_filter:
        where = "WHERE status = :status"
        params["status"] = status_filter
    try:
        async with sessionmaker() as s:
            rows = (
                await s.execute(
                    text(
                        "SELECT id, task_name, status, queue_name, attempts, scheduled_at, args "
                        f"FROM procrastinate_jobs {where} "
                        "ORDER BY id DESC LIMIT :limit"
                    ),
                    params,
                )
            ).all()
    except Exception:  # noqa: BLE001 — queue not installed
        return JobsListOut(items=[])
    return JobsListOut(
        items=[
            JobOut(
                id=int(r[0]), task_name=str(r[1]), status=str(r[2]),
                queue_name=str(r[3]), attempts=int(r[4]),
                scheduled_at=r[5].isoformat() if r[5] else None,
                args=r[6] or {},
            )
            for r in rows
        ]
    )


@admin_router.post("/jobs/{job_id}/retry", response_model=RetryOut)
async def retry_job(job_id: int, request: Request) -> RetryOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        try:
            # Reset `attempts` + flip back to todo. procrastinate's
            # `retry_job_v2` function would be more thorough; this is the
            # minimal manual retry that works regardless of fn version.
            await s.execute(
                text(
                    "UPDATE procrastinate_jobs "
                    "SET status = 'todo', scheduled_at = now(), attempts = 0 "
                    "WHERE id = :id"
                ),
                {"id": job_id},
            )
            await s.commit()
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status_code=500, detail=f"retry failed: {exc}") from exc
    return RetryOut(ok=True, id=job_id)


__all__ = ["admin_router", "router"]
