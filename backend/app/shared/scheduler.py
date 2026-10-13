"""Periodic job registration — the scheduler side of the queue layer.

Procrastinate's ``@app.periodic(cron=…)`` deferrer wraps the task so an
always-on worker auto-enqueues it when the cron fires. On the free host
(no persistent worker) the scheduler is kick-started by the "scheduler
poke" inside ``POST /jobs/drain`` — procrastinate's own
``_defer_periodic_jobs`` call enqueues any periodic whose ``run_at`` has
passed before the drain window starts.

Keeping all periodics in one file makes the full schedule readable at a
glance; moving them into module ``jobs.py`` would scatter the policy. The
cron windows match the queue plan + the architecture review's closing
cron-gap list.
"""

from __future__ import annotations

import logging

from app.shared.orm import LJM_TENANT_ID
from app.shared.queue import app

log = logging.getLogger(__name__)


# For v1 we have one tenant (LJM). Multi-tenant scheduling will fan out
# across `organizations` once a second tenant is seeded.
_TENANT = LJM_TENANT_ID


# ``periodic(cron=…)`` returns a decorator. We rebind the underlying tasks
# so the registry keeps one name per task; the periodic deferrer is a
# metadata attachment, not a new task.


# Cron fires both EST (19:00 UTC) and EDT (20:00 UTC) every day — only one
# matches wall-clock 15:00 America/New_York, but after a long worker outage
# procrastinate backfills everything inside its ``max_delay``, so both can
# enqueue back-to-back. A ``queueing_lock`` makes the second one a no-op
# (``AlreadyEnqueued``) instead of a concurrent run — concurrent crawls
# scanned the same OSM state in parallel and collided on
# ``shipper_candidates_osm_ref_key``.
_CRAWL_LOCK = f"prospecting.crawl_leads:{_TENANT}"


async def _dispatch_crawl() -> None:
    from procrastinate.exceptions import AlreadyEnqueued

    from app.shared.queue import dispatch

    try:
        await dispatch("prospecting.crawl_leads", queueing_lock=_CRAWL_LOCK, tenant_id=_TENANT, trigger="cron")
    except AlreadyEnqueued:
        log.info("crawl_daily: another crawl already queued (lock=%s); skipping", _CRAWL_LOCK)


@app.periodic(cron="0 19 * * *")  # 15:00 America/New_York during EST (UTC 19)
@app.task(name="scheduler.crawl_daily_est", queue="default", pass_context=False)
async def _crawl_daily_est(timestamp: int) -> None:
    await _dispatch_crawl()


@app.periodic(cron="0 20 * * *")  # 15:00 America/New_York during EDT (UTC 20)
@app.task(name="scheduler.crawl_daily_edt", queue="default", pass_context=False)
async def _crawl_daily_edt(timestamp: int) -> None:
    await _dispatch_crawl()


@app.periodic(cron="5 19 * * *")  # 5min after the EST crawl
@app.task(name="scheduler.auto_send_after_crawl", queue="default", pass_context=False)
async def _auto_send_after_crawl(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("outreach.auto_send", tenant_id=_TENANT, dry_run=False)


@app.periodic(cron="*/15 * * * *")  # every 15 min — closes the Gmail cron gap
@app.task(name="scheduler.mail_incremental_tick", queue="default", pass_context=False)
async def _mail_incremental_tick(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("inbox.mail_incremental", tenant_id=_TENANT, mailbox=None)


@app.periodic(cron="0 * * * *")  # every hour — closes the loads cron gap
@app.task(name="scheduler.loads_refresh_tick", queue="default", pass_context=False)
async def _loads_refresh_tick(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("loads.refresh", tenant_id=_TENANT, source_kind=None)


@app.periodic(cron="0 2 * * *")  # 02:00 UTC nightly — gradual historical import
@app.task(name="scheduler.mail_backfill_step_nightly", queue="default", pass_context=False)
async def _mail_backfill_step_nightly(timestamp: int) -> None:
    """Fan ``inbox.mail_backfill_step`` out across the mail-read allowlist.

    Reuses the same allowlist ``mail_incremental`` fans over. One step per
    mailbox per night marches ``backfilled_through_at`` backwards by
    ``window_days`` until the 12-month floor.
    """
    from app.config import get_settings
    from app.integrations.adapters.email.credentials import resolve_mail_read_allowlist
    from app.shared.queue import dispatch

    settings = get_settings()
    allow = resolve_mail_read_allowlist(settings) or set()
    for mbx in sorted(allow):
        await dispatch(
            "inbox.mail_backfill_step",
            tenant_id=_TENANT,
            mailbox=mbx,
        )


@app.periodic(cron="0 3 * * *")  # 03:00 UTC nightly
@app.task(name="scheduler.retention_sweep_nightly", queue="default", pass_context=False)
async def _retention_sweep_nightly(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("inbox.retention_sweep", tenant_id=_TENANT)


@app.periodic(cron="30 3 * * *")  # 03:30 UTC nightly
@app.task(name="scheduler.analysis_nightly_tick", queue="default", pass_context=False)
async def _analysis_nightly_tick(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("analysis.nightly", tenant_id=_TENANT)


@app.periodic(cron="0 6 * * *")  # 06:00 UTC — single daily LoadStop crawl
@app.task(name="scheduler.loadstop_daily_tick", queue="default", pass_context=False)
async def _loadstop_daily_tick(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("loadstop.ingest_tick", tenant_id=_TENANT)


@app.periodic(cron="0 10 * * 1")  # Mondays 10:00 UTC — after EIA publishes
@app.task(name="scheduler.diesel_refresh_weekly", queue="default", pass_context=False)
async def _diesel_refresh_weekly(timestamp: int) -> None:
    from app.shared.queue import dispatch

    await dispatch("rates.refresh_diesel", tenant_id=_TENANT)


__all__: list[str] = []
