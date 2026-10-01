"""Analysis module jobs — nightly fan-out (price-to-win, win probability,
lane seasonality, broker churn, best send time, slow payers, lookalikes).

This file declares the reserved task slots so the scheduler can enqueue
them from day 1; the bodies are owned by
[[ljm-intelligence-data-stats-visualization-plan]] + the analysis module
of the inbox-analysis plan and swap in without touching this file.
"""

from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="analysis.nightly", queue="default", pass_context=False)
async def analysis_nightly(tenant_id: str) -> None:
    """Nightly fan-out: compute broker/lane predictions + lookalikes + objections.

    One session, one transaction — the service's snapshot write (DELETE
    previous rows + INSERT fresh ones) is atomic per table. Running twice
    in a row is harmless: the second run replaces the first's rows.
    """
    set_tenant(TenantId(tenant_id))
    from app.analysis.service import run_nightly
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as session:
            stats = await run_nightly(session)
            await session.commit()
        log.info("analysis.nightly: done %s", stats)
    finally:
        await engine.dispose()
