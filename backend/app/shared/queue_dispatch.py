"""Helper that lifts an HTTP route onto the queue when it's available.

Pattern: every long-running route calls `maybe_dispatch(sessionmaker,
"module.task", **kwargs)` first. When the queue schema exists (Postgres +
procrastinate installed), the job is deferred and the helper returns an
int job id; the route returns a quick "{job_id: N}" envelope and the
worker/drain picks it up. When the queue isn't installed (sqlite dev,
tests, local one-shot) the helper returns None and the route falls back
to running the service inline — the historical behaviour, so existing
tests and the "crawl now" button keep working with no backend change.

Keeping both paths in one helper means every route reads the same two
lines; the backward-compat seam is one place to grep for later.
"""

from __future__ import annotations

import logging

from app.config import get_settings
from app.shared.queue import _is_postgres, dispatch

log = logging.getLogger(__name__)


def _dispatch_enabled() -> bool:
    """Guard: never dispatch from a test environment.

    The test suite seeds hermetic in-memory DB fixtures and asserts on the
    sync result shape of every long-running route. Under TEST_HARNESS=pg16
    the procrastinate schema is present (so the plumbing is exercised by
    `test_queue_pg.py`) but the HTTP tests still want the inline path.
    `app_env == 'test'` is the single switch.
    """
    try:
        return get_settings().app_env != "test"
    except Exception:  # noqa: BLE001
        return True


async def _queue_schema_exists(sessionmaker) -> bool:
    """Cheap probe: `procrastinate_jobs` present on the current DB?"""
    if not _is_postgres():
        return False
    try:
        from sqlalchemy import text

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


async def maybe_dispatch(sessionmaker, task_name: str, /, **kwargs) -> int | None:
    """Dispatch when queue is available; return None to signal "run inline".

    The returned int is a procrastinate job id. On the inline path the
    caller should run its original service body and return a sync result
    (``job_id=None``).
    """
    if not _dispatch_enabled():
        return None
    if not await _queue_schema_exists(sessionmaker):
        return None
    try:
        return await dispatch(task_name, **kwargs)
    except Exception as exc:  # noqa: BLE001
        log.warning("maybe_dispatch(%s) fell back to inline: %s", task_name, exc)
        return None
