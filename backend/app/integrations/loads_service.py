"""Load-boards service — list + source management + ingest.

Thin business layer behind ``app/api/loads.py``. Owns the DB reads/writes
(``Load`` rows, ``SettingsRow.*_configured_at`` fields) and the registry /
adapter calls. The router stays HTTP: ``X-Cron-Secret`` check, pydantic
projection, 404 mapping.

Returns plain dataclasses; raises :class:`UnknownSourceError` for unknown
``kind`` so the router can lift it to a 404.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad
from app.integrations.adapters.loadboard.registry import all_sources, by_kind, enabled_sources
from app.prospecting.models import Lead, Load

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


def _group_hash(raw: RawLoad) -> str:
    """Deterministic sha256[:32] of normalised lane key.

    Same inputs → same hash → group URL survives a re-ingest. Mirrors the
    backfill in migration 0025.
    """
    pickup = ""
    if raw.pickup_date is not None:
        pickup = str(raw.pickup_date)[:10]
    key = "|".join([
        (raw.broker_name or "").strip().lower(),
        (raw.origin_state or "").strip().lower(),
        (raw.dest_state or "").strip().lower(),
        pickup,
        (raw.equipment or "").strip().lower(),
    ])
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:32]


async def _resolve_broker_lead(session: AsyncSession, raw: RawLoad) -> str | None:
    """Match a RawLoad's broker to a :class:`Lead` row.

    Ladder (from the live-loads plan): email → phone → (name, origin_state).
    Returns the Lead id or None. Pure SQL, zero AI.
    """
    if raw.broker_email:
        row = await session.execute(
            select(Lead.id).where(Lead.primary_email == raw.broker_email).limit(1)
        )
        hit = row.scalar_one_or_none()
        if hit:
            return hit
    if raw.broker_phone:
        row = await session.execute(
            select(Lead.id).where(Lead.phone == raw.broker_phone).limit(1)
        )
        hit = row.scalar_one_or_none()
        if hit:
            return hit
    if raw.broker_name and raw.origin_state:
        row = await session.execute(
            select(Lead.id)
            .where(Lead.name == raw.broker_name)
            .where(Lead.state == raw.origin_state)
            .limit(1)
        )
        hit = row.scalar_one_or_none()
        if hit:
            return hit
    return None


async def _store_batch(sessionmaker: Any, raws: list[RawLoad]) -> tuple[int, int]:
    inserted = 0
    skipped = 0
    for raw in raws:
        async with sessionmaker() as s:
            try:
                lead_id = await _resolve_broker_lead(s, raw)
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
                        broker_lead_id=lead_id,
                        dedupe_group_hash=_group_hash(raw),
                    )
                )
                await s.commit()
                inserted += 1
            except IntegrityError:
                await s.rollback()
                skipped += 1
    return inserted, skipped


@dataclass
class LoadGroupRow:
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


def _pick_contact(rows: list[Load]) -> dict:
    """Dedupe tie-break (plan amendment #3): prefer row with phone+email, else newest posted_at."""
    both = [r for r in rows if r.broker_email and r.broker_phone]
    pool = both if both else list(rows)
    pool.sort(key=lambda r: (r.posted_at or datetime.min.replace(tzinfo=UTC)), reverse=True)
    top = pool[0] if pool else None
    if top is None:
        return {"name": "", "email": None, "phone": None, "lead_id": None}
    return {
        "name": top.broker_name or "",
        "email": top.broker_email,
        "phone": top.broker_phone,
        "lead_id": top.broker_lead_id,
    }


async def list_loads_deduped(session: AsyncSession, *, limit: int = 100) -> list[LoadGroupRow]:
    """Return one row per ``dedupe_group_hash``, with multi-source badges.

    Pure SQL — fetches rows + folds in Python. The group hash is deterministic
    (same inputs → same hash → URL survives a re-ingest). Default list hides
    ``booked`` and ``lost`` rows so the operator sees only live work.
    """
    rows = (
        await session.execute(
            select(Load)
            .where(Load.status.in_(("new", "contacted")))
            .order_by(Load.posted_at.desc().nullslast())
        )
    ).scalars().all()
    groups: dict[str, list[Load]] = {}
    order: list[str] = []
    for r in rows:
        gh = r.dedupe_group_hash or ""
        if gh not in groups:
            order.append(gh)
            groups[gh] = []
        groups[gh].append(r)
    out: list[LoadGroupRow] = []
    for gh in order[:limit]:
        bucket = groups[gh]
        # Pick the lowest rate_usd (bands if multi); newest posted_at.
        rates = [r.rate_usd for r in bucket if r.rate_usd is not None]
        min_rate = float(min(rates)) if rates else None
        miles_vals = [r.miles for r in bucket if r.miles is not None]
        miles = int(min(miles_vals)) if miles_vals else None
        posted = max((r.posted_at for r in bucket if r.posted_at is not None), default=None)
        pickup = next((r.pickup_date for r in bucket if r.pickup_date), None)
        rpm = (min_rate / miles) if (min_rate is not None and miles) else None
        src_counts: dict[str, int] = {}
        for r in bucket:
            src_counts[r.source] = src_counts.get(r.source, 0) + 1
        anchor = bucket[0]
        contact = _pick_contact(bucket)
        is_demo = any((r.source or "") == "demo" for r in bucket) or any(
            ((r.raw or {}).get("demo") is True) for r in bucket
        )
        out.append(
            LoadGroupRow(
                group_hash=gh,
                broker=contact,
                origin={"city": anchor.origin_city, "state": anchor.origin_state or ""},
                dest={"city": anchor.dest_city, "state": anchor.dest_state or ""},
                pickup_date=pickup,
                equipment=anchor.equipment,
                rate_usd=min_rate,
                miles=miles,
                rate_per_mile=rpm,
                posted_at=posted,
                sources=[{"kind": k, "count": c} for k, c in sorted(src_counts.items())],
                status=anchor.status,
                status_at=anchor.status_at,
                is_demo=is_demo,
            )
        )
    return out


async def set_group_status(
    sessionmaker: Any, group_hash: str, status: str
) -> int:
    """Flip every row in a dedupe group's ``status`` + ``status_at`` atomically.

    Returns the number of rows updated. Transactional: either every row in
    the group reads the new status, or none do.
    """
    if status not in ("new", "contacted", "booked", "lost"):
        raise ValueError(f"unknown status: {status}")
    now = datetime.now(UTC)
    async with sessionmaker() as s, s.begin():
        result = await s.execute(
            update(Load)
            .where(Load.dedupe_group_hash == group_hash)
            .values(status=status, status_at=now)
        )
        count = int(result.rowcount or 0)
    return count


async def group_contact(
    sessionmaker: Any, group_hash: str
) -> dict | None:
    """Return the best contact in a group — same tie-break as the list."""
    async with sessionmaker() as s:
        rows = (
            await s.execute(select(Load).where(Load.dedupe_group_hash == group_hash))
        ).scalars().all()
    if not rows:
        return None
    anchor = rows[0]
    contact = _pick_contact(rows)
    return {
        "broker": contact,
        "origin": {"city": anchor.origin_city, "state": anchor.origin_state},
        "dest": {"city": anchor.dest_city, "state": anchor.dest_state},
        "pickup_date": anchor.pickup_date,
        "equipment": anchor.equipment,
        "rate_usd": float(anchor.rate_usd) if anchor.rate_usd is not None else None,
        "miles": anchor.miles,
    }


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
