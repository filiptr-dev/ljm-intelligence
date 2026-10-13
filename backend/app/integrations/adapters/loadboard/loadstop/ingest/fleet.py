"""Fleet ingest — LoadStop ``trucks`` service → our ``trucks`` table.

Upsert on ``(tenant_id, unit_number)`` — matches the live unique the
0040 migration added. ``source='loadstop'`` + ``source_ref=<ABP id>`` is
the cross-source dedupe anchor; a loadstop_id lives in ``source_ref`` so
no schema change is needed here.

The 15-min ``trucks`` tick calls this with ``locations_only=True`` to
cheap-refresh ``last_lat/lng/last_seen_at``; the hourly tick calls with
the default so make/model/plate/odometer also refresh.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.models import Truck
from app.integrations.adapters.loadboard.loadstop.ingest.geocode import geocode
from app.shared.orm import LJM_TENANT_ID

SOURCE = "loadstop"


class _ClientLike(Protocol):
    async def paged(self, service, method, *, page_size=200, extra=None, result_path=None): ...


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:  # noqa: BLE001
        return None


def _project(row: dict[str, Any]) -> dict[str, Any]:
    """Project one ``trucks/GetAll`` envelope row.

    LoadStop returns ``{truck: {...}, carrierName, assignedDriverName,
    locationDescription, status, ...}`` — the nested ``truck`` dict carries
    the identifying fields (id, number, make, model, vin, licenseNumber,
    currentMileage), with the live location string on the outer envelope.
    """
    inner = row.get("truck") or row.get("truckDto") or {}
    status_bool = inner.get("status")
    out_of_service = bool(inner.get("outOfOrder"))
    status = "out_of_service" if out_of_service else ("active" if status_bool else "inactive" if status_bool is False else None)
    loc_desc = row.get("locationDescription") or inner.get("locationDescription") or ""
    # City-string → approximate state-centroid pin (no tracking API,
    # no network call). ``geocode`` never raises; it returns ``None``
    # when the string is unparseable or carries no US state code —
    # ingest must never fail on a bad location string.
    coords: tuple[float, float] | None
    try:
        coords = geocode(loc_desc) if loc_desc else None
    except Exception:  # noqa: BLE001
        coords = None
    return {
        "source_ref": str(inner.get("id") or row.get("id") or ""),
        "unit_number": str(inner.get("number") or inner.get("unitNumber") or row.get("truckNumber") or "").strip(),
        "make": inner.get("make"),
        "model": inner.get("model"),
        "vin": inner.get("vin"),
        "plate": inner.get("licenseNumber") or inner.get("plate"),
        "status": status,
        "odometer_miles": inner.get("currentMileage") or inner.get("odometer"),
        "assigned_driver_name": row.get("assignedDriverName") or inner.get("assignedDriverName"),
        # No lat/lng in the paged envelope — only a human string. We
        # parse it into a state-centroid pin (``geocode()`` in the
        # sibling module); real coordinates would come from a tracking
        # endpoint we don't call read-only.
        "last_lat": coords[0] if coords else None,
        "last_lng": coords[1] if coords else None,
        "last_seen_at": None,
        "location_description": loc_desc or None,
    }


async def ingest_trucks(
    session: AsyncSession,
    client: _ClientLike,
    *,
    locations_only: bool = False,
    tenant_id: str = LJM_TENANT_ID,
) -> dict[str, int]:
    """Pull every truck page; upsert into ``trucks``. Returns a count dict."""
    rows_in = 0
    rows_upserted = 0
    # statusFilter=-1 is "all statuses" — without it the server returns
    # the stats envelope with items=[] (discovery 2026-10-12).
    async for page in client.paged("trucks", "GetAll", page_size=200, extra={"statusFilter": -1}):
        for raw in page:
            rows_in += 1
            projected = _project(raw)
            if not projected["unit_number"]:
                continue
            existing = (
                await session.execute(
                    select(Truck).where(
                        Truck.tenant_id == tenant_id,
                        Truck.unit_number == projected["unit_number"],
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                truck = Truck(
                    tenant_id=tenant_id,
                    unit_number=projected["unit_number"],
                    source=SOURCE,
                    source_ref=projected["source_ref"],
                    raw=raw,
                )
                # Full fields on insert
                if projected["status"]:
                    truck.status = projected["status"]
                for k in (
                    "make",
                    "model",
                    "vin",
                    "plate",
                    "odometer_miles",
                    "assigned_driver_name",
                    "last_lat",
                    "last_lng",
                    "last_seen_at",
                ):
                    setattr(truck, k, projected[k])
                session.add(truck)
                rows_upserted += 1
            else:
                # Refresh locations when the envelope carries them; otherwise
                # keep what we had (the paged list has only the string
                # locationDescription, no coordinates).
                if projected["last_lat"] is not None:
                    existing.last_lat = projected["last_lat"]
                if projected["last_lng"] is not None:
                    existing.last_lng = projected["last_lng"]
                if projected["last_seen_at"] is not None:
                    existing.last_seen_at = projected["last_seen_at"]
                if not locations_only:
                    existing.make = projected["make"] or existing.make
                    existing.model = projected["model"] or existing.model
                    existing.vin = projected["vin"] or existing.vin
                    existing.plate = projected["plate"] or existing.plate
                    if projected["status"]:
                        existing.status = projected["status"]
                    existing.odometer_miles = projected["odometer_miles"] or existing.odometer_miles
                    existing.assigned_driver_name = (
                        projected["assigned_driver_name"] or existing.assigned_driver_name
                    )
                    existing.source = SOURCE
                    existing.source_ref = projected["source_ref"] or existing.source_ref
                    existing.raw = raw
                rows_upserted += 1
    await session.flush()
    return {"rows_in": rows_in, "rows_upserted": rows_upserted}
