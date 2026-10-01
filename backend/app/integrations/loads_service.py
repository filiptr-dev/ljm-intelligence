"""Load-boards service — list + source management + ingest.

Thin business layer behind ``app/api/loads.py``. Owns the DB reads/writes
(``Load`` rows, ``SettingsRow.*_configured_at`` fields) and the registry /
adapter calls. The router stays HTTP: ``X-Cron-Secret`` check, pydantic
projection, 404 mapping.

Returns plain dataclasses; raises :class:`UnknownSourceError` for unknown
``kind`` so the router can lift it to a 404.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad
from app.integrations.adapters.loadboard.registry import all_sources, by_kind, enabled_sources
from app.models import Load, SettingsRow

log = logging.getLogger(__name__)


class UnknownSourceError(Exception):
    """Raised when a load-board ``kind`` is not in the registry."""


KIND_TO_CONFIGURED_COL = {
    "dat": "dat_configured_at",
    "chr": "chr_configured_at",
    "loadboard123": "lb123_configured_at",
    "truckstop": "truckstop_configured_at",
}


@dataclass
class LoadRow:
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


@dataclass
class SourceRow:
    kind: str
    enabled: bool
    last_verified_at: datetime | None = None
    reason: str | None = None


@dataclass
class ConnectionTestRow:
    ok: bool
    latency_ms: int
    reason: str | None
    sample_count: int


@dataclass
class RefreshStatsRow:
    kind: str
    read: int
    inserted: int
    skipped: int
    status: str  # "ok" | "disabled" | "error"
    error: str | None = None


@dataclass
class RefreshAllResult:
    items: list[RefreshStatsRow] = field(default_factory=list)


async def _read_configured_at(sessionmaker: Any, kind: str) -> datetime | None:
    col = KIND_TO_CONFIGURED_COL.get(kind)
    if not col:
        return None
    async with sessionmaker() as s:
        row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
        if row is None:
            return None
        return getattr(row, col, None)


async def _store_batch(sessionmaker: Any, raws: list[RawLoad]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
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


async def list_loads(session: AsyncSession, *, limit: int = 50) -> list[LoadRow]:
    rows = (
        await session.execute(
            select(Load).order_by(Load.posted_at.desc().nullslast()).limit(limit)
        )
    ).scalars().all()
    return [
        LoadRow(
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


async def list_sources(sessionmaker: Any, settings: Any) -> list[SourceRow]:
    items: list[SourceRow] = []
    for src in all_sources(settings):
        reason = getattr(src, "reason", None)
        reason_val = reason() if callable(reason) else None
        verified = await _read_configured_at(sessionmaker, src.kind)
        items.append(
            SourceRow(
                kind=src.kind,
                enabled=src.enabled,
                last_verified_at=verified,
                reason=reason_val if not src.enabled else None,
            )
        )
    return items


async def test_source(sessionmaker: Any, settings: Any, kind: str) -> ConnectionTestRow:
    src = by_kind(settings, kind)
    if src is None:
        raise UnknownSourceError(kind)
    result: ConnectionTest = await src.test_connection(settings)
    if result.ok and kind in KIND_TO_CONFIGURED_COL:
        async with sessionmaker() as s:
            row = (
                await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
            ).scalar_one_or_none()
            if row is None:
                row = SettingsRow(id=1)
                s.add(row)
            setattr(row, KIND_TO_CONFIGURED_COL[kind], datetime.now(UTC))
            await s.commit()
    return ConnectionTestRow(
        ok=result.ok,
        latency_ms=result.latency_ms,
        reason=result.reason,
        sample_count=result.sample_count,
    )


async def refresh_source(sessionmaker: Any, settings: Any, kind: str) -> RefreshStatsRow:
    src = by_kind(settings, kind)
    if src is None:
        raise UnknownSourceError(kind)
    if not src.enabled:
        return RefreshStatsRow(kind=kind, read=0, inserted=0, skipped=0, status="disabled")
    try:
        raws = await src.fetch(settings)
    except Exception as exc:  # noqa: BLE001
        log.warning("loads/refresh: %s failed: %s", kind, exc)
        return RefreshStatsRow(
            kind=kind, read=0, inserted=0, skipped=0, status="error", error=str(exc)
        )
    inserted, skipped = await _store_batch(sessionmaker, raws)
    return RefreshStatsRow(
        kind=kind, read=len(raws), inserted=inserted, skipped=skipped, status="ok"
    )


async def refresh_all(sessionmaker: Any, settings: Any) -> RefreshAllResult:
    items: list[RefreshStatsRow] = []
    for src in enabled_sources(settings):
        try:
            raws = await src.fetch(settings)
        except Exception as exc:  # noqa: BLE001
            items.append(
                RefreshStatsRow(
                    kind=src.kind, read=0, inserted=0, skipped=0, status="error", error=str(exc)
                )
            )
            continue
        inserted, skipped = await _store_batch(sessionmaker, raws)
        items.append(
            RefreshStatsRow(
                kind=src.kind,
                read=len(raws),
                inserted=inserted,
                skipped=skipped,
                status="ok",
            )
        )
    return RefreshAllResult(items=items)
