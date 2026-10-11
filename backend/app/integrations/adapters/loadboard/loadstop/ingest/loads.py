"""Loads ingest — LoadStop ``leads`` (Smart Dispatcher) + ``splitLoads``.

Two surfaces, one dedupe anchor:

* ``leads`` + ``leadPastLoads`` + ``leadNegotiations`` → DAT-flavoured
  rows that LoadStop fetches *through* its DAT integration. We mark these
  ``source='loadstop'`` with ``raw.loadstop.subsource`` carrying
  ``'leads'`` or ``'dat_via_loadstop'`` when the DAT marker is present.
* ``splitLoads`` → LJM's own split-loads log; same ``source='loadstop'``,
  ``subsource='split'``.

Dedupe key on the Load table is ``(source, source_ref)``; using
``source='loadstop'`` + the ABP record id makes repeat ingest an upsert.

**Filter at ingest:** east-of-Texas 32 states (``in_operating_area``) +
Dry Van only. Keeps the dedupe surface honest and respects the standing
operating-area rule.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.operating_area import in_operating_area
from app.prospecting.models import Load
from app.shared.orm import LJM_TENANT_ID


SOURCE = "loadstop"
DRY_VAN_WORDS = ("van", "dry van", "dv", "reefer-off")  # 'reefer-off' = van-eligible


class _ClientLike(Protocol):
    async def paged(self, service, method, *, page_size=200, extra=None): ...


def _is_dry_van(equipment: str | None) -> bool:
    if not equipment:
        return False
    v = equipment.lower()
    if "reefer" in v and "reefer-off" not in v:
        return False
    if "flat" in v or "step" in v or "lowboy" in v or "deck" in v:
        return False
    return any(w in v for w in ("van", "dv", "dry"))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except Exception:  # noqa: BLE001
        return None


def _project(row: dict[str, Any], subsource: str) -> dict[str, Any] | None:
    origin_state = ((row.get("originState") or (row.get("origin") or {}).get("state") or "")[:2]).upper() or None
    dest_state = ((row.get("destState") or (row.get("destination") or {}).get("state") or "")[:2]).upper() or None
    equipment = row.get("equipment") or row.get("equipmentType")
    if not in_operating_area(origin_state):
        return None
    if not _is_dry_van(equipment):
        return None
    sid = str(row.get("id") or "")
    if not sid:
        return None
    rate = row.get("rate") or row.get("rateUsd") or row.get("totalRate")
    return {
        "source_ref": f"{subsource}:{sid}",
        "broker_name": row.get("brokerName") or row.get("company") or "",
        "broker_email": row.get("brokerEmail") or row.get("email"),
        "broker_phone": row.get("brokerPhone") or row.get("phone"),
        "origin_city": row.get("originCity") or (row.get("origin") or {}).get("city"),
        "origin_state": origin_state,
        "dest_city": row.get("destCity") or (row.get("destination") or {}).get("city"),
        "dest_state": dest_state,
        "pickup_date": _parse_ts(row.get("pickupDate") or row.get("pickup")),
        "equipment": equipment,
        "rate_usd": float(rate) if rate is not None else None,
        "miles": row.get("miles"),
        "posted_at": _parse_ts(row.get("postedAt")),
        "raw": {"loadstop": {"subsource": subsource, "id": sid}, **row},
    }


async def _ingest_service(
    session: AsyncSession,
    client: _ClientLike,
    *,
    service: str,
    method: str,
    subsource: str,
    tenant_id: str,
) -> tuple[int, int]:
    rows_in = 0
    upserted = 0
    async for page in client.paged(service, method, page_size=200):
        for raw in page:
            rows_in += 1
            p = _project(raw, subsource)
            if p is None:
                continue
            existing = (
                await session.execute(
                    select(Load).where(
                        Load.tenant_id == tenant_id,
                        Load.source == SOURCE,
                        Load.source_ref == p["source_ref"],
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    Load(
                        tenant_id=tenant_id,
                        source=SOURCE,
                        source_ref=p["source_ref"],
                        broker_name=p["broker_name"] or "",
                        broker_email=p["broker_email"],
                        broker_phone=p["broker_phone"],
                        origin_city=p["origin_city"],
                        origin_state=p["origin_state"],
                        dest_city=p["dest_city"],
                        dest_state=p["dest_state"],
                        pickup_date=p["pickup_date"],
                        equipment=p["equipment"],
                        rate_usd=p["rate_usd"],
                        miles=p["miles"],
                        posted_at=p["posted_at"],
                        raw=p["raw"],
                    )
                )
            else:
                # Refresh mutable fields only.
                if p["rate_usd"] is not None:
                    existing.rate_usd = p["rate_usd"]
                existing.raw = p["raw"]
            upserted += 1
    return rows_in, upserted


async def ingest_loads(
    session: AsyncSession,
    client: _ClientLike,
    *,
    tenant_id: str = LJM_TENANT_ID,
) -> dict[str, int]:
    total_in = 0
    total_up = 0
    for service, method, sub in (
        ("leads", "GetAll", "dat_via_loadstop"),
        ("leadPastLoads", "GetAll", "dat_via_loadstop"),
        ("splitLoads", "GetAll", "split"),
    ):
        try:
            i, u = await _ingest_service(session, client, service=service, method=method, subsource=sub, tenant_id=tenant_id)
            total_in += i
            total_up += u
        except Exception as exc:  # noqa: BLE001 — one failing service doesn't kill the batch
            import logging

            logging.getLogger(__name__).warning("loadstop loads ingest %s failed: %s", service, exc)
    await session.flush()
    return {"rows_in": total_in, "rows_upserted": total_up}
