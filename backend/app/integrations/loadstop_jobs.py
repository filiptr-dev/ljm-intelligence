"""LoadStop ingest job — one daily full crawl, serial across services.

Gates: ``LOADSTOP_INGEST_KILL`` env off, and env creds
(``LOADSTOP_USERNAME``/``LOADSTOP_PASSWORD``) present. No per-service DB
toggles — the user wants it always on when creds exist; a toggle is just
a hidden off-switch.

Each per-service run records a ``loadstop_ingest_runs`` row with
``rows_in`` / ``rows_upserted`` / ``status`` so the Sync buttons can
render "Last sync (ok · N rows · 2m ago)".
"""

from __future__ import annotations

import logging
import os
from datetime import UTC, datetime

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.integrations.adapters.loadboard.loadstop.http import LoadstopAuthRequired
from app.integrations.adapters.loadboard.loadstop.session import open_client
from app.shared.orm import LJM_TENANT_ID
from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

LOG = logging.getLogger(__name__)


def _kill_switch() -> bool:
    return os.environ.get("LOADSTOP_INGEST_KILL", "").strip() in ("1", "true", "on", "yes")


def _creds_present() -> bool:
    cfg = get_settings()
    user = (cfg.loadstop_username or "").strip()
    pw = cfg.loadstop_password.get_secret_value() if cfg.loadstop_password else ""
    return bool(user and pw)


async def _record_run(
    session: AsyncSession,
    service: str,
    status: str,
    rows_in: int,
    rows_upserted: int,
    error_msg: str | None,
    started_at: datetime,
) -> None:
    await session.execute(
        text(
            "INSERT INTO loadstop_ingest_runs "
            "(tenant_id, service, started_at, finished_at, status, rows_in, rows_upserted, error_msg) "
            "VALUES (:tid, :svc, :started, :finished, :status, :ri, :ru, :err)"
        ),
        {
            "tid": LJM_TENANT_ID,
            "svc": service,
            "started": started_at,
            "finished": datetime.now(UTC),
            "status": status,
            "ri": rows_in,
            "ru": rows_upserted,
            "err": error_msg,
        },
    )


def _should_run() -> bool:
    """True when the ingest is cleared to proceed."""
    if _kill_switch():
        LOG.info("loadstop ingest skipped: LOADSTOP_INGEST_KILL set")
        return False
    if not _creds_present():
        LOG.info("loadstop ingest skipped: env creds not set")
        return False
    return True


@app.task(name="loadstop.ingest_tick", queue="default", pass_context=False)
async def loadstop_ingest_tick(tenant_id: str = LJM_TENANT_ID) -> None:
    """Full daily crawl — fleet → customers → contacts → loads → rates (serial)."""
    set_tenant(TenantId(tenant_id))
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        if not _should_run():
            return
        try:
            async with open_client(sm) as client:
                await _run_one(sm, client, "trucks", _run_trucks)
                await _run_one(sm, client, "customers", _run_customers)
                await _run_one(sm, client, "contacts", _run_contacts)
                await _run_one(sm, client, "loads", _run_loads)
                await _run_one(sm, client, "rates", _run_rates)
        except LoadstopAuthRequired as exc:
            LOG.warning("loadstop ingest aborted: %s", exc)
    finally:
        await engine.dispose()


# --- per-service wrappers --------------------------------------------------

async def _run_one(sessionmaker, client, service: str, fn) -> None:
    started = datetime.now(UTC)
    status = "ok"
    error = None
    counts: dict[str, int] = {"rows_in": 0, "rows_upserted": 0}
    try:
        async with sessionmaker() as s:
            counts = await fn(s, client)
            await s.commit()
    except Exception as exc:
        status = "error"
        error = str(exc)[:500]
        LOG.exception("loadstop %s failed", service)
    finally:
        async with sessionmaker() as s:
            await _record_run(s, service, status, counts.get("rows_in", 0), counts.get("rows_upserted", 0), error, started)
            await s.commit()


async def _run_trucks(session, client, *, locations_only: bool = False):
    from app.integrations.adapters.loadboard.loadstop.ingest.fleet import ingest_trucks

    return await ingest_trucks(session, client, locations_only=locations_only)


async def _run_customers(session, client):
    from app.integrations.adapters.loadboard.loadstop.ingest.customers import ingest_customers

    return await ingest_customers(session, client)


async def _run_contacts(session, client):
    from app.integrations.adapters.loadboard.loadstop.ingest.customers import ingest_contacts

    return await ingest_contacts(session, client)


async def _run_loads(session, client):
    from app.integrations.adapters.loadboard.loadstop.ingest.loads import ingest_loads

    return await ingest_loads(session, client)


async def _run_rates(session, client):
    from app.integrations.adapters.loadboard.loadstop.ingest.rates import ingest_rates

    return await ingest_rates(session, client)
