"""Loads module jobs — scheduled polling of enabled load-board sources.

Lives under ``app/integrations/`` (not ``app/loads/``) because the whole
loads surface already lives under integrations/. One task registration
file per real module; the module's ``jobs.py`` convention is otherwise
preserved.

Idempotency: :func:`app.integrations.loads_service.refresh_all` upserts on
each source's vendor id (see adapters) — a repeat run only re-UPSERTs the
same rows.
"""

from __future__ import annotations

import logging

from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

log = logging.getLogger(__name__)


@app.task(name="loads.refresh", queue="default", pass_context=False)
async def loads_refresh(tenant_id: str, source_kind: str | None = None) -> None:
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    from app.integrations.loads_service import refresh_all, refresh_source

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        if source_kind:
            stats = await refresh_source(sm, settings, source_kind)
            log.info("loads.refresh: done", extra={"kind": source_kind, "inserted": stats.inserted})
        else:
            result = await refresh_all(sm, settings)
            log.info("loads.refresh: all done", extra={"items": len(result.items)})
    finally:
        await engine.dispose()
