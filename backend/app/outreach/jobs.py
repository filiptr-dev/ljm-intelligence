"""Outreach module jobs — enrichment auto-send (CAN-SPAM compliant).

Idempotency: `app.outreach.service.auto_send` already applies the daily
cap and suppression check — a repeat tick in the same window is a no-op.
"""

from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="outreach.auto_send", queue="default", pass_context=False)
async def enrichment_auto_send(tenant_id: str, dry_run: bool = False) -> None:
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.outreach.service import auto_send

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        result = await auto_send(sessionmaker=sm, settings=settings, dry_run=dry_run)
        log.info(
            "outreach.auto_send: done",
            extra={"status": result.status, "sent": result.sent, "tenant_id": tenant_id},
        )
    finally:
        await engine.dispose()
