"""/api/loadboards/loadstop — slim status + Sync verb.

LoadStop credentials now come from env (``LOADSTOP_USERNAME`` /
``LOADSTOP_PASSWORD`` — user override of the secrets-in-DB rule). This
router no longer accepts a PATCH or a /test; it exposes:

* ``GET /loadboards/loadstop`` — status (configured + session + last sync).
* ``POST /loadboards/loadstop/sync/{service}`` — operator-triggered run,
  ``service in {fleet, customers, loads, rates, all}``.

Everything dispatches ``loadstop.ingest_tick`` — one daily crawl at
06:00 UTC + on-demand Sync buttons replace the old hourly/15-min cron
pair. The ingest tick fans across the four services serially.
"""

from __future__ import annotations

import logging
import os
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import text as _sa_text

from app.config import get_settings
from app.identity.credentials import (
    CredentialVault,
    VaultConfigError,
    VaultNotFound,
)
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/loadboards/loadstop", tags=["loadboards"])


SERVICES: tuple[str, ...] = ("fleet", "customers", "loads", "rates", "all")


class LastSyncBlock(BaseModel):
    fleet: str | None = None
    customers: str | None = None
    loads: str | None = None
    rates: str | None = None


class StateOut(BaseModel):
    configured: bool
    email_masked: str | None = None
    has_session: bool
    last_login_at: str | None = None
    last_sync: LastSyncBlock
    kill_switch_on: bool


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return (local[:2] + "***") + "@" + domain


def _kill_switch_on() -> bool:
    return os.environ.get("LOADSTOP_INGEST_KILL", "").strip() in ("1", "true", "on", "yes")


async def _get_session_bundle(session) -> dict | None:
    try:
        vault = await CredentialVault.for_session(session)
    except VaultConfigError:
        return None
    try:
        return await vault.get(session, TenantId(LJM_TENANT_ID), "loadstop_session", "session")
    except VaultNotFound:
        return None
    except Exception:  # noqa: BLE001
        return None


async def _last_sync_map(session) -> dict[str, str | None]:
    """Latest ``ok`` finished_at per logical service (fleet/customers/loads/rates).

    ``customers`` folds the ``contacts`` sub-service in — operators think
    of brokers as one surface.
    """
    out: dict[str, str | None] = {"fleet": None, "customers": None, "loads": None, "rates": None}
    try:
        rows = (
            await session.execute(
                _sa_text(
                    "SELECT service, MAX(finished_at) AS ts FROM loadstop_ingest_runs "
                    "WHERE status='ok' GROUP BY service"
                )
            )
        ).all()
    except Exception:  # noqa: BLE001 — table may not exist in test harness
        return out
    for svc, ts in rows:
        key = "fleet" if svc in ("trucks", "trucks_locations") else ("customers" if svc in ("customers", "contacts") else svc)
        if key in out and ts is not None:
            iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)
            if out[key] is None or iso > (out[key] or ""):
                out[key] = iso
    return out


@router.get("", response_model=StateOut)
async def get_state(request: Request) -> StateOut:
    cfg = get_settings()
    username = (cfg.loadstop_username or "").strip()
    has_password = bool(cfg.loadstop_password and cfg.loadstop_password.get_secret_value())
    configured = bool(username and has_password)
    async with request.app.state.sessionmaker() as s:
        sess = await _get_session_bundle(s)
        last = await _last_sync_map(s)
    return StateOut(
        configured=configured,
        email_masked=_mask(username) if username else None,
        has_session=bool(sess),
        last_login_at=((sess or {}).get("last_login_at")) if sess else None,
        last_sync=LastSyncBlock(**last),
        kill_switch_on=_kill_switch_on(),
    )


@router.post("/sync/{service}", response_model=dict)
async def kick_sync(service: Literal["fleet", "customers", "loads", "rates", "all"], request: Request) -> dict:
    """Operator-triggered one-shot refresh. Defers the real ingest job.

    Gates: kill switch off + env creds present. The hourly tick already
    fans across every service in order, so `fleet|customers|loads|rates|all`
    all dispatch the same `loadstop.ingest_tick` task; the ingest job
    runs them all serially.
    """
    if service not in SERVICES:
        raise HTTPException(404, f"unknown service: {service}")

    if _kill_switch_on():
        raise HTTPException(409, "kill_switch_on")

    cfg = get_settings()
    username = (cfg.loadstop_username or "").strip()
    has_password = bool(cfg.loadstop_password and cfg.loadstop_password.get_secret_value())
    if not (username and has_password):
        raise HTTPException(409, "not_configured")

    from app.shared.queue import dispatch

    task_name = "loadstop.ingest_tick"
    await dispatch(task_name, tenant_id=LJM_TENANT_ID)
    return {"service": service, "queued": True, "task": task_name}
