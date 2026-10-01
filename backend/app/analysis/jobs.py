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
    """Reserved — fan-out owned by analysis plan. No-op until that lands."""
    set_tenant(TenantId(tenant_id))
    log.info("analysis.nightly: scheduled (bodies owned by analysis plan)")
