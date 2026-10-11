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

from datetime import datetime, timezone
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet.models import Truck
from app.shared.orm import LJM_TENANT_ID


SOURCE = "loadstop"


class _ClientLike(Protocol):
    async def paged(self, service, method, *, page_size=200, extra=None): ...


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _project(row: dict[str, Any]) -> dict[str, Any]:
    loc = row.get("lastLocation") or {}
    return {
        "source_ref": str(row.get("id") or ""),
        "unit_number": str(row.get("unitNumber") or "").strip(),
        "make": row.get("make"),
        "model": row.get("model"),
        "vin": row.get("vin"),
        "plate": row.get("plate"),
        "status": (row.get("status") or "").lower() or None,
        "odometer_miles": row.get("odometer"),
        "assigned_driver_name": row.get("assignedDriverName"),
        "last_lat": loc.get("lat"),
        "last_lng": loc.get("lng"),
        "last_seen_at": _parse_ts(loc.get("movedAt")),
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
    async for page in client.paged("trucks", "GetAll", page_size=200):
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
                # Refresh locations always; refresh the rest only on the full tick.
                existing.last_lat = projected["last_lat"]
                existing.last_lng = projected["last_lng"]
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
