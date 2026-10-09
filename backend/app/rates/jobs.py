"""Rates module jobs — EIA weekly diesel refresh.

Idempotent on (padd, week_of). Absent EIA key → no-op with a logged note
(not an error — graceful degradation; the UI already handles "diesel not
configured" via the service).
"""
from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="rates.refresh_diesel", queue="default", pass_context=False)
async def refresh_diesel(tenant_id: str) -> None:
    """Pull the latest row per PADD from EIA and upsert `diesel_prices`.
    Scheduled weekly (Mondays) in the ops cron."""
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.adapters.eia.client import EIAClient, load_api_key
    from app.rates.repository import upsert_diesel_prices

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as s:
            key = await load_api_key(s)
            if not key:
                log.info("rates.refresh_diesel: no EIA key configured — skipping.")
                return
            client = EIAClient(api_key=key)
            rows = await client.latest_by_padd()
            if not rows:
                log.warning("rates.refresh_diesel: EIA returned zero rows.")
                return
            count = await upsert_diesel_prices(
                s, [(r.padd, r.week_of, r.price_usd) for r in rows]
            )
            log.info("rates.refresh_diesel: upserted %d rows", count)
    finally:
        await engine.dispose()
