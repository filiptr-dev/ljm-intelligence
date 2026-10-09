"""DemoFleetSource — the dummy fleet, read back from rows tagged ``source='demo'``.

No network, no credentials, no env. It exists to prove the port end to end
before a real vendor lands: it maps the seeded rows into the same vendor-neutral
``FleetSnapshot`` a real adapter would return.
"""

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.fleet import repository as repo
from app.fleet.ports import (
    FleetSnapshot,
    RawDefect,
    RawDocument,
    RawInspection,
    RawMaintenance,
    RawUnit,
)

SOURCE = "demo"


class DemoFleetSource:
    name = SOURCE
    enabled = True

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def snapshot(self, tenant_id: str) -> FleetSnapshot:
        rows = await repo.snapshot_rows(self._session, tenant_id, SOURCE)
        ref = {u.id: u.source_ref or u.unit_number for u in rows["units"]}
        return FleetSnapshot(
            source=SOURCE,
            units=[
                RawUnit(
                    source_ref=u.source_ref or u.unit_number, unit_number=u.unit_number, kind=u.kind, vin=u.vin,
                    make=u.make, model=u.model, year=u.year, plate=u.plate, equipment=u.equipment, status=u.status,
                    odometer_miles=u.odometer_miles, home_base_city=u.home_base_city, home_base_state=u.home_base_state,
                    assigned_driver_name=u.assigned_driver_name,
                    last_lat=None if u.last_lat is None else float(u.last_lat),
                    last_lng=None if u.last_lng is None else float(u.last_lng),
                    last_seen_at=u.last_seen_at, raw=u.raw or {},
                )
                for u in rows["units"]
            ],
            inspections=[
                RawInspection(
                    unit_ref=ref[i.truck_id], inspected_at=i.inspected_at, result=i.result,
                    inspector_name=i.inspector_name, odometer_at_inspection=i.odometer_at_inspection, notes=i.notes,
                    raw=i.raw or {},
                )
                for i in rows["inspections"]
            ],
            defects=[
                RawDefect(
                    unit_ref=ref[d.truck_id], severity=d.severity, title=d.title, reported_at=d.reported_at,
                    status=d.status, description=d.description, resolved_at=d.resolved_at, raw=d.raw or {},
                )
                for d in rows["defects"]
            ],
            maintenance=[
                RawMaintenance(
                    unit_ref=ref[m.truck_id], kind=m.kind, title=m.title, scheduled_for=m.scheduled_for,
                    completed_at=m.completed_at, odometer_at=m.odometer_at,
                    cost_usd=None if m.cost_usd is None else float(m.cost_usd), notes=m.notes, raw=m.raw or {},
                )
                for m in rows["maintenance"]
            ],
            documents=[
                RawDocument(
                    unit_ref=ref[d.truck_id], kind=d.kind, expires_on=d.expires_on, number=d.number,
                    issued_on=d.issued_on, file_url=d.file_url, raw=d.raw or {},
                )
                for d in rows["documents"]
            ],
        )
