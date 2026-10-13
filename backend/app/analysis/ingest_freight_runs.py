"""freight_runs — real-data ingest.

Three surfaces today, one target table:

* **LoadStop `splitLoads/GetAllWithSP`** — split-loads grid, one row per
  split load. Upsert as ``source='loadstop'``, ``source_ref='splitLoads:{id}'``.
* **LoadStop `loads/GetAllWithSP`** — regular-loads grid, same shape.
  Upsert as ``source='loadstop'``, ``source_ref='loads:{id}'``.
* **Inbox `loads` table** — rate-con rows with a lane + a rate derived
  from the message. Upsert as ``source='inbox'``,
  ``source_ref='inbox:{load.id}'``. Skipped when a LoadStop trip with the
  same lane + pickup day already exists (the rate-con is the pre-image of
  that trip).

What goes into a run:

* lane + pickup_at + delivery_at + miles (nullable) + equipment
* revenue_usd — the shipper rate from the feed; **never fabricated**
* cost_fuel_usd — estimated as ``miles × diesel/gal / 6.5 mpg`` when
  diesel + miles are both known; NULL otherwise. The lanes UI labels it
  as an estimate.
* cost_driver / cost_load / cost_dispatch — **NULL unless the feed gave
  it to us.** The lanes page coalesces NULLs to 0 at aggregate time and
  the per-run view shows "no cost data".
* origin/dest lat+lng come from state centroids (we don't have city-level
  geocoding on this install, and ``freight_runs`` requires non-null
  lat/lng for the map).

Teaching note: everything flows through
``_upsert_run(session, tenant_id, data)`` so there's exactly one place
that decides "update vs insert", and it keys off ``(tenant_id, source,
source_ref)`` — the partial-unique index 0050 added. That's the
idempotency contract.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import FreightRun
from app.analysis.operating_area import in_operating_area
from app.integrations.adapters.loadboard.loadstop.ingest.geocode import STATE_CENTROIDS
from app.prospecting.models import Load
from app.rates.models import DieselPrice
from app.shared.orm import LJM_TENANT_ID

__all__ = [
    "backfill_runs",
    "ingest_inbox_runs",
    "ingest_loadstop_runs",
]

MPG = 6.5
# Fallback diesel if ``diesel_prices`` is empty — the US-average diesel
# EIA was reporting on 2026-10-13, flagged in the UI as "fallback".
FALLBACK_DIESEL = 3.80


class _ClientLike(Protocol):
    async def paged(
        self, service: str, method: str, *, page_size: int = 200,
        extra: dict[str, Any] | None = None, result_path: str | None = None,
    ): ...


# ---- shared helpers ----------------------------------------------------------


def _parse_ts(value: Any) -> datetime | None:
    if not value:
        return None
    s = str(value)
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _state(value: Any) -> str | None:
    if not value:
        return None
    s = str(value).strip().upper()[:2]
    return s or None


def _coords(state: str | None) -> tuple[float, float] | None:
    if not state:
        return None
    return STATE_CENTROIDS.get(state)


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


async def _latest_diesel(session: AsyncSession) -> float | None:
    row = (
        await session.execute(
            select(DieselPrice.price_usd).order_by(DieselPrice.week_of.desc()).limit(1)
        )
    ).first()
    if row is None:
        return None
    return float(row[0])


def _fuel_estimate(miles: float | None, diesel: float | None) -> float | None:
    if not miles or not diesel:
        return None
    return round(miles * diesel / MPG, 2)


async def _upsert_run(
    session: AsyncSession, *, tenant_id: str, source: str, source_ref: str,
    data: dict[str, Any],
) -> str:
    """Insert or refresh a single ``freight_runs`` row. Returns
    ``'inserted'`` / ``'updated'`` / ``'skipped'`` so the caller can
    count outcomes without a second round-trip."""
    existing = (
        await session.execute(
            select(FreightRun).where(
                FreightRun.tenant_id == tenant_id,
                FreightRun.source == source,
                FreightRun.source_ref == source_ref,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(FreightRun(
            tenant_id=tenant_id, source=source, source_ref=source_ref, **data,
        ))
        return "inserted"
    # Refresh the mutable fields only — origin/dest + pickup_at don't move.
    for k in ("revenue_usd", "cost_fuel_usd", "miles", "equipment", "delivery_at", "raw"):
        v = data.get(k)
        if v is not None:
            setattr(existing, k, v)
    return "updated"


# ---- LoadStop ingest --------------------------------------------------------


def _project_loadstop(row: dict[str, Any], subsource: str) -> dict[str, Any] | None:
    """Project one row from a splitLoads / loads grid into the
    freight_runs shape. Returns ``None`` when the row is unusable
    (missing lane, no pickup date, out of operating area)."""
    o_state = _state(row.get("pickupState"))
    d_state = _state(row.get("deliveryState"))
    o_city = (row.get("pickupCity") or "").strip() or None
    d_city = (row.get("deliveryCity") or "").strip() or None
    pickup = _parse_ts(row.get("pickupDate"))
    delivery = _parse_ts(row.get("deliveryDate")) or pickup
    if not (o_state and d_state and o_city and d_city and pickup and delivery):
        return None
    if not in_operating_area(o_state):
        return None
    o_ll = _coords(o_state)
    d_ll = _coords(d_state)
    if not o_ll or not d_ll:
        return None
    miles = _num(row.get("calculatedTotalMiles") or row.get("loadedMiles") or row.get("distance"))
    rate = _num(row.get("totalAmount") or row.get("rate") or row.get("primaryFees"))
    sid = row.get("id")
    if sid is None:
        return None
    return {
        "source_ref": f"{subsource}:{sid}",
        "broker_name": (row.get("customerName") or row.get("brokerName") or "")[:255] or None,
        "origin_city": o_city[:128],
        "origin_state": o_state,
        "dest_city": d_city[:128],
        "dest_state": d_state,
        "origin_lat": Decimal(f"{o_ll[0]:.5f}"),
        "origin_lng": Decimal(f"{o_ll[1]:.5f}"),
        "dest_lat": Decimal(f"{d_ll[0]:.5f}"),
        "dest_lng": Decimal(f"{d_ll[1]:.5f}"),
        "miles": int(miles) if miles else None,
        "equipment": row.get("equipment") or ("Van" if row.get("vanType") == 1 else None),
        "pickup_at": pickup,
        "delivery_at": delivery,
        "revenue_usd": Decimal(f"{rate:.2f}") if rate is not None else None,
        "raw": {"loadstop": {"subsource": subsource, "id": sid, "loadStatus": row.get("loadStatus")}, **row},
    }


_LOADSTOP_FEEDS: tuple[tuple[str, str, str, str], ...] = (
    ("splitLoads", "GetAllWithSP", "splitLoads", "spLoadsGridResult"),
    ("loads", "GetAllWithSP", "loads", "spLoadsGridResult"),
)


async def ingest_loadstop_runs(
    session: AsyncSession, client: _ClientLike,
    *, tenant_id: str = LJM_TENANT_ID,
    months: int | None = None,
) -> dict[str, int]:
    """Pull completed trips off every LoadStop grid we have access to and
    upsert into ``freight_runs``. Only trips with ``pickup_at`` within
    ``months`` back are kept when ``months`` is given.
    """
    cutoff: datetime | None = (
        datetime.now(UTC) - timedelta(days=30 * months) if months else None
    )
    diesel = await _latest_diesel(session) or FALLBACK_DIESEL
    totals = {"rows_in": 0, "inserted": 0, "updated": 0, "skipped": 0}
    for service, method, subsource, path in _LOADSTOP_FEEDS:
        try:
            async for page in client.paged(service, method, page_size=200, result_path=path):
                for raw in page:
                    totals["rows_in"] += 1
                    p = _project_loadstop(raw, subsource)
                    if p is None:
                        totals["skipped"] += 1
                        continue
                    if cutoff and p["pickup_at"] < cutoff:
                        totals["skipped"] += 1
                        continue
                    source_ref = p.pop("source_ref")
                    p["cost_fuel_usd"] = (
                        Decimal(f"{_fuel_estimate(p['miles'], diesel):.2f}")
                        if _fuel_estimate(p["miles"], diesel) is not None
                        else None
                    )
                    outcome = await _upsert_run(
                        session, tenant_id=tenant_id, source="loadstop",
                        source_ref=source_ref, data=p,
                    )
                    totals[outcome] += 1
        except Exception as exc:  # noqa: BLE001 — one feed down shouldn't kill the batch
            import logging

            logging.getLogger(__name__).warning(
                "freight_runs loadstop ingest %s/%s failed: %s", service, method, exc,
            )
    await session.flush()
    return totals


# ---- Inbox ingest -----------------------------------------------------------


def _split_city_state(value: str | None) -> tuple[str, str] | None:
    if not value:
        return None
    s = value.strip()
    if "," not in s:
        return None
    city, _, st = s.rpartition(",")
    city = city.strip()
    st = st.strip().upper()[:2]
    if not city or len(st) != 2:
        return None
    return (city, st)


async def ingest_inbox_runs(
    session: AsyncSession, *, tenant_id: str = LJM_TENANT_ID,
    months: int | None = None,
) -> dict[str, int]:
    """Promote inbox rate-con ``loads`` rows into ``freight_runs``.

    Only rows that have both a derived lane AND a derived rate make the
    cut — we never fabricate missing lanes or rates to pad the page. A
    row that is already represented as a LoadStop trip on the same lane
    + pickup day is skipped (the rate-con is upstream of the LoadStop
    trip and we don't want to count the same move twice).
    """
    cutoff = datetime.now(UTC) - timedelta(days=30 * months) if months else None
    diesel = await _latest_diesel(session) or FALLBACK_DIESEL
    totals = {"rows_in": 0, "inserted": 0, "updated": 0, "skipped": 0}
    stmt = (
        select(Load).where(
            Load.tenant_id == tenant_id,
            Load.source == "inbox",
            Load.derived_origin.is_not(None),
            Load.derived_dest.is_not(None),
            Load.derived_rate_usd.is_not(None),
        )
    )
    rows: Iterable[Load] = (await session.execute(stmt)).scalars().all()
    for load in rows:
        totals["rows_in"] += 1
        o = _split_city_state(load.derived_origin)
        d = _split_city_state(load.derived_dest)
        if not o or not d or not in_operating_area(o[1]):
            totals["skipped"] += 1
            continue
        pickup = load.pickup_date or load.posted_at or load.ingested_at
        if not pickup:
            totals["skipped"] += 1
            continue
        if pickup.tzinfo is None:
            pickup = pickup.replace(tzinfo=UTC)
        if cutoff and pickup < cutoff:
            totals["skipped"] += 1
            continue
        o_ll = _coords(o[1])
        d_ll = _coords(d[1])
        if not o_ll or not d_ll:
            totals["skipped"] += 1
            continue
        # De-dup against an existing LoadStop trip on the same lane +
        # pickup day — the rate-con usually arrives *before* the trip
        # lands in LoadStop.
        clash = (
            await session.execute(
                select(func.count()).select_from(FreightRun).where(and_(
                    FreightRun.tenant_id == tenant_id,
                    FreightRun.source == "loadstop",
                    FreightRun.origin_state == o[1],
                    FreightRun.dest_state == d[1],
                    FreightRun.pickup_at >= pickup.replace(hour=0, minute=0, second=0, microsecond=0),
                    FreightRun.pickup_at < pickup.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1),
                ))
            )
        ).scalar_one()
        if clash:
            totals["skipped"] += 1
            continue
        miles = load.miles
        rate = float(load.derived_rate_usd) if load.derived_rate_usd is not None else None
        data = {
            "broker_name": (load.broker_name or "")[:255] or None,
            "broker_lead_id": load.broker_lead_id,
            "origin_city": o[0][:128],
            "origin_state": o[1],
            "dest_city": d[0][:128],
            "dest_state": d[1],
            "origin_lat": Decimal(f"{o_ll[0]:.5f}"),
            "origin_lng": Decimal(f"{o_ll[1]:.5f}"),
            "dest_lat": Decimal(f"{d_ll[0]:.5f}"),
            "dest_lng": Decimal(f"{d_ll[1]:.5f}"),
            "miles": int(miles) if miles else None,
            "equipment": load.equipment,
            "pickup_at": pickup,
            "delivery_at": pickup + timedelta(days=1),  # rate-con has no delivery stamp
            "revenue_usd": Decimal(f"{rate:.2f}") if rate is not None else None,
            "cost_fuel_usd": (
                Decimal(f"{_fuel_estimate(miles, diesel):.2f}")
                if _fuel_estimate(miles, diesel) is not None else None
            ),
            "raw": {"inbox": {"load_id": load.id, "subject_hash": load.dedupe_group_hash}},
        }
        outcome = await _upsert_run(
            session, tenant_id=tenant_id, source="inbox",
            source_ref=f"inbox:{load.id}", data=data,
        )
        totals[outcome] += 1
    await session.flush()
    return totals


# ---- orchestrator ----------------------------------------------------------


async def backfill_runs(
    session: AsyncSession, client: _ClientLike,
    *, tenant_id: str = LJM_TENANT_ID,
    months: int | None = None,
) -> dict[str, dict[str, int]]:
    """Full backfill: LoadStop first (so inbox dedupe sees them), then
    inbox. Returns per-source totals so the CLI can print a report.
    """
    loadstop = await ingest_loadstop_runs(session, client, tenant_id=tenant_id, months=months)
    inbox = await ingest_inbox_runs(session, tenant_id=tenant_id, months=months)
    return {"loadstop": loadstop, "inbox": inbox}
