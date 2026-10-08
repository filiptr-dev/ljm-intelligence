"""/loads routes — thin router over ``app.integrations.loads_service``.

Reads come under ``user_only``. The ``refresh-all`` cron endpoint accepts
``X-Cron-Secret``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.config import Settings
from app.integrations.loads_service import (
    UnknownSourceError,
    _store_batch,
    group_contact as svc_group_contact,
    list_loads as svc_list_loads,
    list_loads_deduped as svc_list_loads_deduped,
    list_sources as svc_list_sources,
    refresh_all as svc_refresh_all,
    refresh_source as svc_refresh_source,
    set_group_status as svc_set_group_status,
    test_source as svc_test_source,
)
from app.shared.cron_auth import check_secret

log = logging.getLogger(__name__)

router = APIRouter(prefix="/loads", tags=["loads"])


class LoadOut(BaseModel):
    id: int
    source: str
    broker_name: str
    origin_city: str | None
    origin_state: str | None
    dest_city: str | None
    dest_state: str | None
    pickup_date: datetime | None
    equipment: str | None
    rate_usd: float | None
    miles: int | None
    posted_at: datetime | None


class LoadGroupOut(BaseModel):
    group_hash: str
    broker: dict
    origin: dict
    dest: dict
    pickup_date: datetime | None
    equipment: str | None
    rate_usd: float | None
    miles: int | None
    rate_per_mile: float | None
    posted_at: datetime | None
    sources: list[dict]
    status: str
    status_at: datetime | None
    is_demo: bool = False


class LoadsListOut(BaseModel):
    items: list[LoadGroupOut]


class PasteIn(BaseModel):
    text: str = Field(min_length=1, max_length=50000)


class StatusIn(BaseModel):
    status: Literal["new", "contacted", "booked", "lost"]


class StatusOut(BaseModel):
    group_hash: str
    status: str
    rows_updated: int


class InquiryOut(BaseModel):
    subject: str
    body: str
    compose_url: str
    broker_email: str | None
    broker_phone: str | None


class SourceOut(BaseModel):
    kind: str
    enabled: bool
    last_verified_at: datetime | None = None
    reason: str | None = None


class SourcesOut(BaseModel):
    items: list[SourceOut]


class ConnectionTestOut(BaseModel):
    ok: bool
    latency_ms: int
    reason: str | None
    sample_count: int


class RefreshStatsOut(BaseModel):
    kind: str
    read: int
    inserted: int
    skipped: int
    status: Literal["ok", "disabled", "error"]
    error: str | None = None


class RefreshAllOut(BaseModel):
    items: list[RefreshStatsOut]
    # Set when the refresh is dispatched to the worker; frontend polls
    # `/jobs/{id}` and refetches the loads board on success.
    job_id: int | None = None


@router.get("", response_model=LoadsListOut)
async def list_loads(request: Request, limit: int = Query(default=100, ge=1, le=500)) -> LoadsListOut:
    """Deduped group rows — one entry per lane, with multi-source badges.

    Rows in ``booked`` / ``lost`` are hidden so the operator only sees
    actionable work. The old flat list stays available at ``/loads/all``
    for admin/debug callers.
    """
    async with request.app.state.sessionmaker() as s:
        groups = await svc_list_loads_deduped(s, limit=limit)
    return LoadsListOut(items=[LoadGroupOut(**g.__dict__) for g in groups])


@router.get("/all", response_model=dict)
async def list_loads_flat(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> dict:
    """Flat, un-grouped list — useful for debugging the dedupe view."""
    async with request.app.state.sessionmaker() as s:
        rows = await svc_list_loads(s, limit=limit)
    return {"items": [r.__dict__ for r in rows]}


@router.get("/sources", response_model=SourcesOut)
async def list_sources(request: Request) -> SourcesOut:
    settings: Settings = request.app.state.settings
    rows = await svc_list_sources(request.app.state.sessionmaker, settings)
    return SourcesOut(items=[SourceOut(**r.__dict__) for r in rows])


@router.post("/sources/{kind}/test", response_model=ConnectionTestOut)
async def test_source(kind: str, request: Request) -> ConnectionTestOut:
    settings: Settings = request.app.state.settings
    try:
        result = await svc_test_source(request.app.state.sessionmaker, settings, kind)
    except UnknownSourceError as exc:
        raise HTTPException(404, f"unknown source: {kind}") from exc
    return ConnectionTestOut(**result.__dict__)


@router.post("/sources/{kind}/refresh", response_model=RefreshStatsOut)
async def refresh_source(kind: str, request: Request) -> RefreshStatsOut:
    settings: Settings = request.app.state.settings
    try:
        result = await svc_refresh_source(request.app.state.sessionmaker, settings, kind)
    except UnknownSourceError as exc:
        raise HTTPException(404, f"unknown source: {kind}") from exc
    return RefreshStatsOut(**result.__dict__)


@router.post("/sources/refresh-all", response_model=RefreshAllOut)
async def refresh_all(
    request: Request,
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> RefreshAllOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)

    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        request.app.state.sessionmaker,
        "loads.refresh",
        tenant_id=LJM_TENANT_ID,
        source_kind=None,
    )
    if job_id is not None:
        from app.integrations.jobs_router import kick_in_process_drain
        background.add_task(
            kick_in_process_drain, request.app.state.sessionmaker, settings,
            seconds=settings.jobs_in_process_kick_seconds,
        )
        return RefreshAllOut(items=[], job_id=job_id)

    result = await svc_refresh_all(request.app.state.sessionmaker, settings)
    return RefreshAllOut(items=[RefreshStatsOut(**r.__dict__) for r in result.items])


__all__ = ["router"]


@router.post("/paste", response_model=LoadsListOut)
async def paste_loads(payload: PasteIn, request: Request) -> LoadsListOut:
    """Operator pastes a block of broker text; AI turns it into load rows.

    Owner-only guard is TODO once a real auth decorator is agreed; v1 is
    behind the same cookie session as the rest of ``/loads``. NullProvider
    (no API key) returns zero items, zero crash.
    """
    import hashlib

    from app.integrations.adapters.ai import provider as ai_provider
    from app.integrations.loads_extract import extract_loads_from_text

    settings: Settings = request.app.state.settings
    ref = f"paste:{hashlib.sha256(payload.text.encode()).hexdigest()[:16]}"
    provider = ai_provider.get_for("inbox_analysis", settings=settings)
    raws = await extract_loads_from_text(provider, payload.text, source="paste", source_ref=ref)
    if not raws:
        return LoadsListOut(items=[])
    await _store_batch(request.app.state.sessionmaker, raws)
    async with request.app.state.sessionmaker() as s:
        groups = await svc_list_loads_deduped(s, limit=100)
    return LoadsListOut(items=[LoadGroupOut(**g.__dict__) for g in groups])


@router.post("/{group_hash}/status", response_model=StatusOut)
async def set_status(group_hash: str, payload: StatusIn, request: Request) -> StatusOut:
    """Flip every row in a dedupe group's status atomically.

    Transactional — either every row reads the new status, or none do. A
    group that no longer has any rows (hash collision, race) returns 404.
    """
    try:
        updated = await svc_set_group_status(
            request.app.state.sessionmaker, group_hash, payload.status
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    if updated == 0:
        raise HTTPException(404, f"group not found: {group_hash}")
    return StatusOut(group_hash=group_hash, status=payload.status, rows_updated=updated)


@router.post("/{group_hash}/inquiry", response_model=InquiryOut)
async def group_inquiry(group_hash: str, request: Request) -> InquiryOut:
    """Build a load-inquiry email via the existing ``/email/draft`` builder.

    Zero new builder — we reuse the single email path with
    ``purpose='load_inquiry'`` so the voice stays consistent with every
    other outbound message LJM sends.
    """
    ctx = await svc_group_contact(request.app.state.sessionmaker, group_hash)
    if ctx is None:
        raise HTTPException(404, f"group not found: {group_hash}")
    broker = ctx["broker"]
    lane = f"{ctx['origin'].get('state') or '?'} → {ctx['dest'].get('state') or '?'}"
    pickup = ctx["pickup_date"].isoformat()[:10] if ctx["pickup_date"] else "flexible"
    equipment = ctx.get("equipment") or "any equipment"
    rate = f"${ctx['rate_usd']:,.0f}" if ctx.get("rate_usd") else "open rate"
    subject = f"LJM — interest on {lane} {pickup} ({equipment})"
    body = (
        f"Hi {broker.get('name') or 'there'},\n\n"
        f"I'd like to cover your {lane} load on {pickup}, {equipment}, listed at {rate}.\n"
        f"We can move today — reply with best rate and MC packet instructions.\n\n"
        f"— LJM Dispatch"
    )
    # Build a mailto: compose URL so the operator can one-click into their
    # mail client without needing Gmail OAuth to be configured.
    from urllib.parse import quote

    to_addr = broker.get("email") or ""
    compose_url = f"mailto:{to_addr}?subject={quote(subject)}&body={quote(body)}"
    return InquiryOut(
        subject=subject,
        body=body,
        compose_url=compose_url,
        broker_email=broker.get("email"),
        broker_phone=broker.get("phone"),
    )


# ---- Session blob round-trip (GitHub Actions runner persistence) ----------
# The agent sidecar lives on a stateless runner; the only durable store is
# the DB vault. These two endpoints are tiny — just encrypt a storage_state
# blob so a run picks up where the last one left off.

class SessionBlobIn(BaseModel):
    # ``blob`` not ``json`` — pydantic warns on fields that shadow BaseModel.
    blob: str = Field(min_length=2, max_length=1_000_000)


class SessionBlobOut(BaseModel):
    source: str
    present: bool
    saved_at: str | None = None


@router.get("/sources/{src}/session", response_model=SessionBlobOut)
async def get_session_blob(
    src: str, request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> SessionBlobOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    from fastapi.responses import JSONResponse

    from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.tenant import TenantId

    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError:
            return SessionBlobOut(source=src, present=False)
        try:
            bundle = await vault.get(s, TenantId(LJM_TENANT_ID), f"loadboard_session_{src}", "storage_state")
        except VaultNotFound:
            return SessionBlobOut(source=src, present=False)
    # Return the raw blob under a header-ish side channel? Instead echo in
    # body — the endpoint is behind X-Cron-Secret.
    return JSONResponse(
        {
            "source": src,
            "present": True,
            "saved_at": str(bundle.get("saved_at") or ""),
            "json": bundle.get("json"),
        }
    )


@router.post("/sources/{src}/session", response_model=SessionBlobOut)
async def set_session_blob(
    src: str, payload: SessionBlobIn, request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> SessionBlobOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    from app.identity.credentials import CredentialVault, VaultConfigError
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.tenant import TenantId

    saved_at = datetime.now(UTC).isoformat()
    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            raise HTTPException(500, f"vault_unavailable:{exc}") from exc
        await vault.put(
            s, TenantId(LJM_TENANT_ID), f"loadboard_session_{src}", "storage_state",
            {"json": payload.blob, "saved_at": saved_at},
        )
        await s.commit()
    return SessionBlobOut(source=src, present=True, saved_at=saved_at)


@router.delete("/demo", response_model=dict)
async def purge_demo_loads(request: Request) -> dict:
    """Delete every demo-seeded row (``source IN ('demo','demo2')``).

    Operator path for the "scraper now has real data, flush the demo" moment.
    Idempotent — running twice returns the second ``deleted=0``.
    """
    from sqlalchemy import delete as _sa_delete

    from app.prospecting.models import Load

    async with request.app.state.sessionmaker() as s:
        result = await s.execute(
            _sa_delete(Load).where(Load.source.in_(("demo", "demo2")))
        )
        await s.commit()
    return {"deleted": int(result.rowcount or 0)}
