"""/api/loadboards/loadstop — Settings UI + sync verb.

Owner-only, like the sibling ``/settings/connectors/loadboard`` router.
This is a *separate* top-level because LoadStop is a TMS (fleet +
customers + loads + rates), not a plain load-board source.
"""

from __future__ import annotations

import logging
from datetime import UTC
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import select

from app.identity.credentials import (
    CredentialVault,
    VaultConfigError,
    VaultNotFound,
)
from app.identity.models import SettingsRow
from app.integrations.adapters.loadboard.loadstop.login import perform_login
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

LOG = logging.getLogger(__name__)

router = APIRouter(prefix="/loadboards/loadstop", tags=["loadboards"])


DRIVER_VALUES = {"off", "api"}  # 'agent' deliberately NOT allowed


class ToggleBlock(BaseModel):
    fleet: bool = True
    customers: bool = True
    loads: bool = True
    rates: bool = True


class LastSyncBlock(BaseModel):
    fleet: str | None = None
    customers: str | None = None
    loads: str | None = None
    rates: str | None = None


class StateOut(BaseModel):
    driver: str
    status: str
    email_set: bool
    email_masked: str | None = None
    has_session: bool
    last_login_at: str | None = None
    toggles: ToggleBlock
    last_sync: LastSyncBlock
    dat_connected: bool = False


class PatchIn(BaseModel):
    email: EmailStr | None = None
    password: str | None = Field(default=None, min_length=1, max_length=1024)
    one_time_code: str | None = Field(default=None, max_length=32)
    driver: Literal["off", "api"] | None = None
    toggles: ToggleBlock | None = None


class TestOut(BaseModel):
    status: str  # 'ok' | 'bad_credentials' | 'needs_code' | 'blocked' | 'unknown' | 'not_configured'
    detail: str | None = None


def _mask(email: str | None) -> str | None:
    if not email or "@" not in email:
        return None
    local, _, domain = email.partition("@")
    return (local[:2] + "***") + "@" + domain


async def _get_settings(session) -> SettingsRow:
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row is None:
        row = SettingsRow(id=1)
        session.add(row)
    return row


async def _get_login_bundle(session) -> dict | None:
    try:
        vault = await CredentialVault.for_session(session)
    except VaultConfigError:
        return None
    try:
        return await vault.get(session, TenantId(LJM_TENANT_ID), "loadstop_login", "password")
    except VaultNotFound:
        return None
    except Exception:  # noqa: BLE001
        return None


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


@router.get("", response_model=StateOut)
async def get_state(request: Request) -> StateOut:
    async with request.app.state.sessionmaker() as s:
        row = await _get_settings(s)
        login = await _get_login_bundle(s)
        sess = await _get_session_bundle(s)
    email = str((login or {}).get("username") or "")
    return StateOut(
        driver=row.loads_loadstop_driver,
        status="ok" if (login and sess) else ("configured" if login else "not_configured"),
        email_set=bool(email),
        email_masked=_mask(email),
        has_session=bool(sess),
        last_login_at=((sess or {}).get("last_login_at")) if sess else None,
        toggles=ToggleBlock(
            fleet=row.loadstop_import_fleet,
            customers=row.loadstop_import_customers,
            loads=row.loadstop_import_loads,
            rates=row.loadstop_import_rates,
        ),
        last_sync=LastSyncBlock(),
        dat_connected=False,
    )


@router.patch("", response_model=StateOut)
async def patch_state(payload: PatchIn, request: Request) -> StateOut:
    """Save credentials (write-only) + driver + toggles. Never echoes the password back."""
    async with request.app.state.sessionmaker() as s:
        row = await _get_settings(s)
        if payload.driver is not None:
            if payload.driver not in DRIVER_VALUES:
                raise HTTPException(400, f"invalid driver: {payload.driver}")
            row.loads_loadstop_driver = payload.driver
        if payload.toggles is not None:
            row.loadstop_import_fleet = payload.toggles.fleet
            row.loadstop_import_customers = payload.toggles.customers
            row.loadstop_import_loads = payload.toggles.loads
            row.loadstop_import_rates = payload.toggles.rates
        # Credentials path — only when both email + password supplied; we
        # never store a half-pair. The GET above will never return the pw.
        if payload.email and payload.password:
            try:
                vault = await CredentialVault.for_session(s)
            except VaultConfigError as exc:
                raise HTTPException(503, f"vault_unavailable:{exc}") from exc
            bundle = {"username": str(payload.email), "password": payload.password}
            if payload.one_time_code:
                bundle["one_time_code"] = payload.one_time_code
            await vault.put(s, TenantId(LJM_TENANT_ID), "loadstop_login", "password", bundle)
        await s.commit()
    return await get_state(request)


@router.post("/test", response_model=TestOut)
async def test_connection(request: Request) -> TestOut:
    """Attempt login with the stored creds. Writes the session on success."""
    async with request.app.state.sessionmaker() as s:
        bundle = await _get_login_bundle(s)
        if not bundle or not bundle.get("username") or not bundle.get("password"):
            return TestOut(status="not_configured", detail="save email+password in Settings first")
        result = await perform_login(
            str(bundle["username"]),
            str(bundle["password"]),
            one_time_code=bundle.get("one_time_code"),
        )
        if result.status == "ok" and result.cookies:
            try:
                vault = await CredentialVault.for_session(s)
            except VaultConfigError as exc:
                return TestOut(status="unknown", detail=f"vault_unavailable:{exc}")
            from datetime import datetime

            await vault.put(
                s,
                TenantId(LJM_TENANT_ID),
                "loadstop_session",
                "session",
                {
                    "cookies": result.cookies,
                    "last_login_at": datetime.now(UTC).isoformat(),
                },
            )
            await s.commit()
        return TestOut(status=result.status, detail=result.detail)


@router.post("/sync/{service}", response_model=dict)
async def kick_sync(service: str, request: Request) -> dict:
    """Operator-triggered one-shot refresh. Defers the real ingest job.

    Gated the same way the scheduler ticks are gated — driver must be
    'api', a session cookie must be stored, kill switch must be off.
    A refused request is explicit (409) rather than silently queued; the
    operator sees "fix the connector first".
    """
    import os as _os

    if service not in {"fleet", "customers", "loads", "rates"}:
        raise HTTPException(404, f"unknown service: {service}")

    if _os.environ.get("LOADSTOP_INGEST_KILL", "").strip() in ("1", "true", "on", "yes"):
        raise HTTPException(409, "LOADSTOP_INGEST_KILL is set — ingest is disabled")

    async with request.app.state.sessionmaker() as s:
        row = await _get_settings(s)
        if row.loads_loadstop_driver != "api":
            raise HTTPException(409, "LoadStop driver is not 'api' — enable it in Settings")
        sess = await _get_session_bundle(s)
    if not sess or not sess.get("cookies"):
        raise HTTPException(409, "no stored LoadStop session — run Test with credentials first")

    from app.shared.queue import dispatch

    # Both the full-ingest tick (hourly job) and the trucks-locations tick
    # (15-min job) honour the Settings toggles internally, so a /sync/fleet
    # that uses the trucks tick is cheap; the other three dispatch the full
    # tick which will no-op the services whose toggle is off.
    task_name = "loadstop.trucks_tick" if service == "fleet" else "loadstop.ingest_tick"
    await dispatch(task_name, tenant_id=LJM_TENANT_ID)
    return {"service": service, "queued": True, "task": task_name}
