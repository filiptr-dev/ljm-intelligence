"""/loads routes — board list + source management.

Reads come under ``user_only``. The ``refresh-all`` cron endpoint accepts
``X-Cron-Secret``.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.api._auth import check_secret
from app.config import Settings
from app.models import Load, SettingsRow
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad
from app.integrations.adapters.loadboard.registry import all_sources, by_kind, enabled_sources

log = logging.getLogger(__name__)

router = APIRouter(prefix="/loads", tags=["loads"])

KIND_TO_CONFIGURED_COL = {
    "dat": "dat_configured_at",
    "chr": "chr_configured_at",
    "loadboard123": "lb123_configured_at",
    "truckstop": "truckstop_configured_at",
}


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


class LoadsListOut(BaseModel):
    items: list[LoadOut]


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


async def _read_configured_at(request: Request, kind: str) -> datetime | None:
    col = KIND_TO_CONFIGURED_COL.get(kind)
    if not col:
        return None
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
        if row is None:
            return None
        return getattr(row, col, None)


async def _store_batch(request: Request, raws: list[RawLoad]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    sessionmaker = request.app.state.sessionmaker
    for raw in raws:
        async with sessionmaker() as s:
            try:
                s.add(
                    Load(
                        source=raw.source,
                        source_ref=raw.source_ref,
                        broker_name=raw.broker_name,
                        broker_email=raw.broker_email,
                        broker_phone=raw.broker_phone,
                        origin_city=raw.origin_city,
                        origin_state=raw.origin_state,
                        dest_city=raw.dest_city,
                        dest_state=raw.dest_state,
                        pickup_date=raw.pickup_date,
                        equipment=raw.equipment,
                        rate_usd=raw.rate_usd,
                        miles=raw.miles,
                        posted_at=raw.posted_at,
                        raw=raw.raw,
                    )
                )
                await s.commit()
                inserted += 1
            except IntegrityError:
                await s.rollback()
                skipped += 1
    return inserted, skipped


@router.get("", response_model=LoadsListOut)
async def list_loads(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> LoadsListOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        rows = (await s.execute(select(Load).order_by(Load.posted_at.desc().nullslast()).limit(limit))).scalars().all()
    return LoadsListOut(
        items=[
            LoadOut(
                id=r.id,
                source=r.source,
                broker_name=r.broker_name,
                origin_city=r.origin_city,
                origin_state=r.origin_state,
                dest_city=r.dest_city,
                dest_state=r.dest_state,
                pickup_date=r.pickup_date,
                equipment=r.equipment,
                rate_usd=float(r.rate_usd) if r.rate_usd is not None else None,
                miles=r.miles,
                posted_at=r.posted_at,
            )
            for r in rows
        ]
    )


@router.get("/sources", response_model=SourcesOut)
async def list_sources(request: Request) -> SourcesOut:
    settings: Settings = request.app.state.settings
    items: list[SourceOut] = []
    for src in all_sources(settings):
        reason = getattr(src, "reason", None)
        reason_val = reason() if callable(reason) else None
        verified = await _read_configured_at(request, src.kind)
        items.append(
            SourceOut(
                kind=src.kind,
                enabled=src.enabled,
                last_verified_at=verified,
                reason=reason_val if not src.enabled else None,
            )
        )
    return SourcesOut(items=items)


@router.post("/sources/{kind}/test", response_model=ConnectionTestOut)
async def test_source(kind: str, request: Request) -> ConnectionTestOut:
    settings: Settings = request.app.state.settings
    src = by_kind(settings, kind)
    if src is None:
        raise HTTPException(404, f"unknown source: {kind}")
    result: ConnectionTest = await src.test_connection(settings)
    if result.ok and kind in KIND_TO_CONFIGURED_COL:
        sessionmaker = request.app.state.sessionmaker
        async with sessionmaker() as s:
            row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
            if row is None:
                row = SettingsRow(id=1)
                s.add(row)
            setattr(row, KIND_TO_CONFIGURED_COL[kind], datetime.now(UTC))
            await s.commit()
    return ConnectionTestOut(
        ok=result.ok,
        latency_ms=result.latency_ms,
        reason=result.reason,
        sample_count=result.sample_count,
    )


@router.post("/sources/{kind}/refresh", response_model=RefreshStatsOut)
async def refresh_source(kind: str, request: Request) -> RefreshStatsOut:
    settings: Settings = request.app.state.settings
    src = by_kind(settings, kind)
    if src is None:
        raise HTTPException(404, f"unknown source: {kind}")
    if not src.enabled:
        return RefreshStatsOut(kind=kind, read=0, inserted=0, skipped=0, status="disabled")
    try:
        raws = await src.fetch(settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("loads/refresh: %s failed: %s", kind, exc)
        return RefreshStatsOut(kind=kind, read=0, inserted=0, skipped=0, status="error", error=str(exc))
    inserted, skipped = await _store_batch(request, raws)
    return RefreshStatsOut(kind=kind, read=len(raws), inserted=inserted, skipped=skipped, status="ok")


@router.post("/sources/refresh-all", response_model=RefreshAllOut)
async def refresh_all(
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> RefreshAllOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    items: list[RefreshStatsOut] = []
    for src in enabled_sources(settings):
        try:
            raws = await src.fetch(settings)
        except Exception as exc:  # noqa: BLE001
            items.append(RefreshStatsOut(kind=src.kind, read=0, inserted=0, skipped=0, status="error", error=str(exc)))
            continue
        inserted, skipped = await _store_batch(request, raws)
        items.append(RefreshStatsOut(kind=src.kind, read=len(raws), inserted=inserted, skipped=skipped, status="ok"))
    return RefreshAllOut(items=items)


__all__ = ["router"]
