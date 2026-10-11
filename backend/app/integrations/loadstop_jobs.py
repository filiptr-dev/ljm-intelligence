"""LoadStop ingest jobs — hourly (full) + 15-min (trucks locations only).

Both are no-ops when:

* ``LOADSTOP_INGEST_KILL=1`` env flag is set (circuit breaker — this is
  the only env the LoadStop surface reads, and it carries a boolean,
  not a secret).
* ``settings.loads_loadstop_driver != 'api'``.
* No session cookie is stored in the vault (login hasn't been Tested
  yet in Settings).

Each per-service run records a ``loadstop_ingest_runs`` row with
``rows_in`` / ``rows_upserted`` / ``status`` so the Settings page can
render "Last sync (ok · N rows · 2m ago)".
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.shared.orm import LJM_TENANT_ID
from app.shared.queue import app
from app.shared.tenant import TenantId, set_tenant

from app.integrations.adapters.loadboard.loadstop.http import LoadstopAuthRequired
from app.integrations.adapters.loadboard.loadstop.session import open_client


LOG = logging.getLogger(__name__)


def _kill_switch() -> bool:
    return os.environ.get("LOADSTOP_INGEST_KILL", "").strip() in ("1", "true", "on", "yes")


async def _read_settings(session: AsyncSession) -> SettingsRow | None:
    return (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()


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
            "finished": datetime.now(timezone.utc),
            "status": status,
            "ri": rows_in,
            "ru": rows_upserted,
            "err": error_msg,
        },
    )


async def _should_run(sessionmaker) -> SettingsRow | None:
    """Returns the SettingsRow when the ingest is cleared to proceed; else None."""
    if _kill_switch():
        LOG.info("loadstop ingest skipped: LOADSTOP_INGEST_KILL set")
        return None
    async with sessionmaker() as s:
        row = await _read_settings(s)
    if row is None or row.loads_loadstop_driver != "api":
        return None
    return row


@app.task(name="loadstop.ingest_tick", queue="default", pass_context=False)
async def loadstop_ingest_tick(tenant_id: str = LJM_TENANT_ID) -> None:
    """Hourly — fleet / customers / contacts / loads / rates per toggles."""
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        srow = await _should_run(sm)
        if srow is None:
            return
        try:
            async with open_client(sm) as client:
                # Each service runs serial under the client's own semaphore.
                if srow.loadstop_import_fleet:
                    await _run_one(sm, client, "trucks", _run_trucks)
                if srow.loadstop_import_customers:
                    await _run_one(sm, client, "customers", _run_customers)
                    await _run_one(sm, client, "contacts", _run_contacts)
                if srow.loadstop_import_loads:
                    await _run_one(sm, client, "loads", _run_loads)
                if srow.loadstop_import_rates:
                    await _run_one(sm, client, "rates", _run_rates)
        except LoadstopAuthRequired as exc:
            LOG.warning("loadstop ingest aborted: %s", exc)
    finally:
        await engine.dispose()


@app.task(name="loadstop.trucks_tick", queue="default", pass_context=False)
async def loadstop_trucks_tick(tenant_id: str = LJM_TENANT_ID) -> None:
    """15-min — just truck locations (cheap, serial)."""
    set_tenant(TenantId(tenant_id))
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        srow = await _should_run(sm)
        if srow is None or not srow.loadstop_import_fleet:
            return
        try:
            async with open_client(sm) as client:
                await _run_one(sm, client, "trucks_locations", lambda s, c: _run_trucks(s, c, locations_only=True))
        except LoadstopAuthRequired as exc:
            LOG.warning("loadstop trucks tick aborted: %s", exc)
    finally:
        await engine.dispose()


# --- per-service wrappers --------------------------------------------------

async def _run_one(sessionmaker, client, service: str, fn) -> None:
    started = datetime.now(timezone.utc)
    status = "ok"
    error = None
    counts: dict[str, int] = {"rows_in": 0, "rows_upserted": 0}
    try:
        async with sessionmaker() as s:
            counts = await fn(s, client)
            await s.commit()
    except Exception as exc:  # noqa: BLE001
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
