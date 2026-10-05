"""Prospecting module jobs — thin procrastinate wrappers.

Each ``@app.task`` here opens a tenant-bound UoW and delegates to its
module's ``service.py`` / ``pipeline`` body. No business logic lives in
this file; it is the queue-side mirror of the HTTP router.
"""

from __future__ import annotations

import logging

from app.shared.logging import set_job_id
from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(
    name="prospecting.crawl_leads",
    queue="default",
    pass_context=False,
)
async def crawl_leads(
    tenant_id: str, trigger: str = "cron", limit: int | None = None, run_id: str | None = None
) -> None:
    """Run one crawl cycle for ``tenant_id``.

    Idempotency: the pipeline is keyed on the daily ``run_id`` — a second
    invocation on the same day no-ops at the FMCSA source level (dedupe
    via MC/DOT/domain partial-unique indexes).
    """
    set_tenant(TenantId(tenant_id))
    if run_id:
        set_job_id(run_id)
    log.info("crawl_leads: start", extra={"tenant_id": tenant_id, "trigger": trigger})

    # Import here so the worker's `import_paths` can load this module even
    # if the pipeline modules fail at import time (defensive — the task
    # fails loudly instead of poisoning registration).
    from app.config import get_settings
    from app.pipeline.run import _new_run_id, abort_crawl_run, run_crawl

    settings = get_settings()
    # The app factory wires sessionmaker into app.state; from a worker
    # process we build one directly from settings.
    from app.db import create_engine, create_sessionmaker

    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        rid = run_id or _new_run_id()
        try:
            await run_crawl(sm, settings, trigger=trigger, fmcsa_limit=limit, run_id=rid)
        except BaseException as exc:
            # run_crawl records its own terminal status for ordinary errors; this
            # covers what escapes it (cancellation, a DB error on the final write)
            # so the crawl_runs row never stays `running` after the job is gone.
            try:
                await abort_crawl_run(
                    sm, rid, tenant_id=tenant_id, error=f"crawl job ended early: {type(exc).__name__}: {exc}"
                )
            except Exception:  # never mask the original failure
                log.exception("crawl_leads: could not mark run %s as error", rid)
            raise
    finally:
        await engine.dispose()
