"""Procrastinate `App` + dispatch helpers — the Laravel-style queue layer.

Three surfaces every caller should know about:

* :data:`app` — the module-level ``procrastinate.App``. Workers import this.
* :func:`dispatch` — enqueue a task by name from any non-transactional path.
* :func:`defer_on` — enqueue a task on the **same** psycopg connection the
  caller is already using for the business transaction (Laravel's
  ``afterCommit`` done right: a rolled-back business transaction leaves no
  ghost job row; a committed one guarantees the job is visible).

Design notes
------------
* Jobs are registered via ``@app.task`` in each business module's
  ``jobs.py``. Nothing cross-module imports a job function directly — the
  worker reaches them via ``procrastinate.App.import_paths``.
* The worker uses the **direct** Neon URL (``DATABASE_URL_DIRECT``) because
  the pooled URL (PgBouncer transaction mode) does not support ``LISTEN /
  NOTIFY``. The API process keeps using the pooled URL — only the queue
  layer needs the direct branch.
* ``defer_on(session, …)`` extracts the raw ``psycopg.AsyncConnection`` from
  the SQLAlchemy ``AsyncSession`` and hands it to procrastinate's connector
  as an external connection, so the INSERT lands inside the same
  transaction as the business write. See ``AC3`` in the queue plan.
"""

from __future__ import annotations

import logging
from typing import Any

from procrastinate import App, PsycopgConnector
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings, get_settings

log = logging.getLogger(__name__)


# Every module that owns tasks is listed here; the worker imports them so
# the task registry is populated before `.run_worker_async()` is called.
# Keep this list in sync with the module `jobs.py` files that exist.
IMPORT_PATHS: list[str] = [
    "app.prospecting.jobs",
    "app.outreach.jobs",
    "app.inbox.jobs",
    "app.analysis.jobs",
    "app.integrations.loads_jobs",
    "app.integrations.mail_jobs",
    "app.shared.scheduler",
]


def _connector_from_settings(settings: Settings | None = None) -> PsycopgConnector:
    """Build the procrastinate connector from the direct (non-pooler) URL.

    Falls back to ``database_url`` when the direct URL isn't configured so
    local dev with one Postgres still works. The pooled URL on Neon does
    NOT support ``LISTEN/NOTIFY`` — the worker will fall back to polling,
    which is correct but noisier; prefer ``DATABASE_URL_DIRECT`` in prod.
    """
    s = settings or get_settings()
    url = s.database_url_direct or s.database_url
    # procrastinate wants the raw postgres URL — strip the SQLAlchemy
    # `+psycopg` suffix if present. On sqlite (dev/tests) hand back a
    # harmless localhost conninfo; `_is_postgres()` guards every call site
    # that would actually try to open it.
    if url.startswith("postgresql+psycopg://"):
        url = "postgresql://" + url.removeprefix("postgresql+psycopg://")
    elif not url.startswith(("postgresql://", "postgres://")):
        url = "postgresql://noop@localhost:5432/noop"
    return PsycopgConnector(conninfo=url)


# Build the App lazily so test-time imports of this module don't open a DB
# connection. The connector attaches on first open.
app: App = App(connector=_connector_from_settings(), import_paths=IMPORT_PATHS)


def _is_postgres() -> bool:
    s = get_settings()
    url = s.database_url_direct or s.database_url
    return url.startswith(("postgresql://", "postgresql+psycopg://", "postgres://"))


async def dispatch(task_name: str, /, **kwargs: Any) -> int | None:
    """Enqueue a registered task by name. Non-transactional.

    Returns the procrastinate job id, or ``None`` if the queue layer is
    disabled (e.g. SQLite in local tests). Use :func:`defer_on` when the
    caller is in the middle of a SQLAlchemy transaction — the job INSERT
    will ride that transaction and roll back with it.
    """
    if not _is_postgres():
        return None
    try:
        # If called from inside a worker (the App is already open, e.g. a
        # periodic task re-dispatching real work), reuse that open connector
        # — closing + reopening our own context would kill the surrounding
        # worker's pool mid-flight and crash the current job.
        already_open = getattr(app.connector, "_async_pool", None) is not None
        if already_open:
            job = app.configure_task(name=task_name)
            job_id = await job.defer_async(**kwargs)
            return int(job_id)
        async with app.open_async():
            job = app.configure_task(name=task_name)
            job_id = await job.defer_async(**kwargs)
            return int(job_id)
    except Exception as exc:  # noqa: BLE001
        log.warning("dispatch(%s) failed: %s", task_name, exc)
        return None


async def defer_on(
    session: AsyncSession,
    task_name: str,
    /,
    *,
    queueing_lock: str | None = None,
    **kwargs: Any,
) -> int | None:
    """Enqueue a task on the session's own connection.

    This is the Laravel ``dispatch(…)->afterCommit()`` pattern: the job row
    INSERT writes inside the current SQLAlchemy transaction. If the caller
    rolls back, the job row goes with it — no ghost jobs. If the caller
    commits, the job becomes visible atomically with the business state.

    Call site contract: ``session`` is an open ``AsyncSession`` *inside* a
    ``session.begin()`` block (or the service's UoW scope). Do not commit
    here; the session boundary owns commit.
    """
    if not _is_postgres():
        return None
    try:
        import json

        from sqlalchemy import text

        # Executing through the SQLAlchemy session binds the INSERT to the
        # session's active transaction automatically — no raw psycopg
        # cursor needed. Use named bindparams to avoid the `%` placeholder
        # trap in psycopg's parser.
        result = await session.execute(
            text(
                "SELECT procrastinate_defer_jobs_v1(ARRAY["
                "ROW(:qname, :task, 0, NULL, :qlock, CAST(:args AS jsonb), NULL)"
                "::procrastinate_job_to_defer_v1"
                "])"
            ),
            {
                "qname": "default",
                "task": task_name,
                "qlock": queueing_lock,
                "args": json.dumps(kwargs),
            },
        )
        row = result.first()
        if not row:
            return None
        ids = row[0]
        return int(ids[0]) if ids else None
    except Exception as exc:  # noqa: BLE001
        log.warning("defer_on(%s) failed: %s", task_name, exc)
        return None


async def jobs_health(session: AsyncSession) -> dict[str, Any]:
    """Snapshot of queue state for `/health` + the `/admin/jobs` page.

    Returns ``{queued, running, failed_last_24h, oldest_queued_age_s}``.
    When the queue schema isn't installed (SQLite local path) this returns
    zeros so the health check still succeeds.
    """
    from sqlalchemy import text

    try:
        row = (
            await session.execute(
                text(
                    """
                    SELECT
                      COUNT(*) FILTER (WHERE status = 'todo')                                        AS queued,
                      COUNT(*) FILTER (WHERE status = 'doing')                                       AS running,
                      COUNT(*) FILTER (WHERE status = 'failed'
                                       AND scheduled_at > now() - interval '24 hours')               AS failed_last_24h,
                      COALESCE(
                        EXTRACT(EPOCH FROM (now() - MIN(scheduled_at)))
                        FILTER (WHERE status = 'todo'),
                        0
                      )::int                                                                         AS oldest_queued_age_s
                    FROM procrastinate_jobs
                    """
                )
            )
        ).first()
        if row is None:
            return _empty_jobs_health()
        return {
            "queued": int(row[0] or 0),
            "running": int(row[1] or 0),
            "failed_last_24h": int(row[2] or 0),
            "oldest_queued_age_s": int(row[3] or 0),
        }
    except Exception:  # noqa: BLE001 — queue not installed / sqlite
        return _empty_jobs_health()


def _empty_jobs_health() -> dict[str, Any]:
    return {
        "queued": 0,
        "running": 0,
        "failed_last_24h": 0,
        "oldest_queued_age_s": 0,
    }


__all__ = ["app", "defer_on", "dispatch", "jobs_health"]
