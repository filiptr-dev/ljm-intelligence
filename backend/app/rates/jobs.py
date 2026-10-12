"""Rates module jobs — EIA weekly diesel refresh.

Idempotent on (padd, week_of). Default path is a keyless scrape of EIA's
public weekly tables (``app.integrations.adapters.eia.scraper``) so a
fresh demo install needs no secrets to populate ``diesel_prices``. If an
EIA API key happens to be stored in the vault we prefer the structured
v2 API — same shape, more robust — but the scrape remains the fallback.
Parse failures are logged and the job returns cleanly; it never raises.
"""
from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="rates.refresh_diesel", queue="default", pass_context=False)
async def refresh_diesel(tenant_id: str) -> None:
    """Pull the latest row per PADD and upsert ``diesel_prices``.
    Scheduled weekly (Mondays) in the ops cron."""
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.adapters.eia import scraper as eia_scraper
    from app.integrations.adapters.eia.client import EIAClient, load_api_key
    from app.rates.repository import upsert_diesel_prices

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with sm() as s:
            rows: list = []
            key = await load_api_key(s)
            if key:
                try:
                    client = EIAClient(api_key=key)
                    api_rows = await client.latest_by_padd()
                    rows = [(r.padd, r.week_of, r.price_usd) for r in api_rows]
                except Exception as exc:  # noqa: BLE001 — fall through to scrape
                    log.warning("rates.refresh_diesel: EIA API failed (%s); scraping public page.", exc)
            if not rows:
                scraped = await eia_scraper.fetch_latest_by_padd()
                rows = [(r.padd, r.week_of, r.price_usd) for r in scraped]
            if not rows:
                log.warning("rates.refresh_diesel: no diesel rows obtained — nothing to upsert.")
                return
            count = await upsert_diesel_prices(s, rows)
            log.info("rates.refresh_diesel: upserted %d rows", count)
    finally:
        await engine.dispose()
