"""Seed the fleet the way migration 0038 does, but through the ORM (sqlite or PG)."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, update

from app.analysis.models import FreightRun
from app.fleet.models import Truck, TruckDefect, TruckDocument, TruckInspection, TruckMaintenance
from tests.analysis.lanes_fixtures import TENANT, demo_rows

_FILE = Path(__file__).resolve().parents[2] / "migrations" / "versions" / "0038_demo_fleet.py"


def load_migration():
    spec = importlib.util.spec_from_file_location("mig0038", _FILE)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def now() -> datetime:
    return datetime.now(UTC)


async def clear_fleet(sm) -> None:
    """On the PG harness the first test of a session still sees the migration's own seed; start clean."""
    async with sm() as s:
        await s.execute(update(FreightRun).values(truck_id=None))
        for m in (TruckDocument, TruckMaintenance, TruckDefect, TruckInspection, Truck):
            await s.execute(delete(m))
        await s.commit()


async def seed_fleet(sm, at: datetime | None = None, tenant: str = TENANT) -> None:
    mig = load_migration()
    at = at or now()
    fleet = mig.generate_fleet(at)
    async with sm() as s:
        runs = [FreightRun(**{**r, "tenant_id": tenant}) for r in demo_rows(at)]
        units = {
            u["unit_number"]: Truck(**{**u, "tenant_id": tenant, "source": "demo", "raw": {"demo": True}})
            for u in fleet["units"]
        }
        s.add_all([*units.values(), *runs])
        await s.flush()
        insp = {}
        for i in fleet["inspections"]:
            row = TruckInspection(
                tenant_id=tenant, source="demo", truck_id=units[i["unit_number"]].id,
                **{k: v for k, v in i.items() if k != "unit_number"},
            )
            s.add(row)
            insp[(i["unit_number"], i["inspected_at"])] = row
        await s.flush()
        for d in fleet["defects"]:
            link = insp.get((d["unit_number"], d["inspected_at"])) if d["inspected_at"] else None
            s.add(TruckDefect(
                tenant_id=tenant, source="demo", truck_id=units[d["unit_number"]].id,
                inspection_id=link.id if link else None,
                **{k: v for k, v in d.items() if k not in ("unit_number", "inspected_at")},
            ))
        for m in fleet["maintenance"]:
            s.add(TruckMaintenance(
                tenant_id=tenant, source="demo", truck_id=units[m["unit_number"]].id,
                **{k: v for k, v in m.items() if k != "unit_number"},
            ))
        for d in fleet["documents"]:
            s.add(TruckDocument(
                tenant_id=tenant, source="demo", truck_id=units[d["unit_number"]].id,
                **{k: v for k, v in d.items() if k != "unit_number"},
            ))
        plan = mig.assign_runs(
            [{"id": r.id, "equipment": r.equipment, "pickup_at": r.pickup_at, "delivery_at": r.delivery_at} for r in runs],
            [{**u, "id": units[u["unit_number"]].id} for u in fleet["units"] if u["kind"] == "truck"],
            at,
        )
        for r in runs:
            if r.id in plan:
                r.truck_id = plan[r.id]["truck_id"]
        placed = mig.last_positions(
            [{**u, "id": units[u["unit_number"]].id} for u in fleet["units"]],
            [{"id": r.id, "pickup_at": r.pickup_at, "delivery_at": r.delivery_at,
              "lat": float(r.origin_lat), "lng": float(r.origin_lng)} for r in runs],
            plan, at,
        )
        for u in units.values():
            u.last_lat, u.last_lng, u.last_seen_at = placed[u.id]
        await s.commit()
