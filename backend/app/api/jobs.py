"""Jobs API — the free-host drain endpoint + minimal admin surface.

* ``POST /jobs/drain?seconds=25`` — bounded worker tick, protected by
  CRON_SECRET. The GitHub Actions ``queue-drain.yml`` posts here every
  5 minutes; on the user's own infra the always-on worker makes this
  endpoint redundant (safe to keep wired).
* ``GET  /jobs/{id}`` — show one job row (status, attempts, last error).
* ``GET  /admin/jobs`` — owner-only list of queued/running/failed.
* ``POST /jobs/{id}/retry`` — owner-only one-click retry.

Queueing lock: the drain itself uses ``queueing_lock='jobs.drain'`` so two
overlapping GitHub Actions runs no-op the second caller instead of double-
draining. Combined with ``SKIP LOCKED`` on the workers this is belt + braces.
"""

from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, Header, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import text

from app.api._auth import check_secret
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


async def _drain_once(sessionmaker, *, seconds: int) -> DrainOut:
    """Bounded in-process drain. Reused by `/jobs/drain` AND by on-demand
    HTTP routes that need the worker to pick up their fresh job within
    seconds (not up to 5 min when waiting for the cron drain).

    The advisory lock on ``hashtext('jobs.drain')`` is the single
    serialising point: a background kick that fires in the same instant
    as a GitHub Actions cron tick sees ``got_lock=False`` and no-ops.
    """
    if not await _queue_installed(sessionmaker):
        return DrainOut(ran=0, succeeded=0, failed=0, remaining=0, reason="queue schema not installed")

    async with sessionmaker() as s:
        lock_row = (
            await s.execute(text("SELECT pg_try_advisory_lock(hashtext('jobs.drain'))"))
        ).first()
        got_lock = bool(lock_row and lock_row[0])

    if not got_lock:
        remaining = await _count_queued(sessionmaker)
        return DrainOut(
            ran=0, succeeded=0, failed=0, remaining=remaining,
            skipped_overlap=True, reason="another drain in progress",
        )

    try:
        from app.shared.queue import app as queue_app

        before = await _status_counts(sessionmaker)

        async with queue_app.open_async():
            # Enqueue any periodic jobs whose run_at has passed. On an
            # always-on worker this is automatic; on free we poke it here.
            try:
                await queue_app._register_builtin_tasks()  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 — older/newer procrastinate paths
                pass

            try:
                await asyncio.wait_for(
                    queue_app.run_worker_async(
                        queues=["default"],
                        wait=False,
                        install_signal_handlers=False,
                        listen_notify=False,
                    ),
                    timeout=float(seconds),
                )
            except asyncio.TimeoutError:
                pass  # expected — bounded tick ending

        after = await _status_counts(sessionmaker)
        ran = max(0, (after.get("succeeded", 0) - before.get("succeeded", 0)) +
                       (after.get("failed", 0) - before.get("failed", 0)))
        succeeded = max(0, after.get("succeeded", 0) - before.get("succeeded", 0))
        failed = max(0, after.get("failed", 0) - before.get("failed", 0))
        return DrainOut(
            ran=ran, succeeded=succeeded, failed=failed,
            remaining=after.get("todo", 0),
        )
    finally:
        async with sessionmaker() as s:
            await s.execute(text("SELECT pg_advisory_unlock(hashtext('jobs.drain'))"))
            await s.commit()


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
