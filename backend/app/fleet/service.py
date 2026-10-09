"""fleet — composes the repository reads into page payloads.

KPIs come from the ``freight_runs`` that carry a ``truck_id`` (assigned by the
seed today; by the real feed later). Cost per mile = (fuel + driver + load +
dispatch) / miles, the same cost model the lanes page uses.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.db import Session as AsyncSession  # the request session (an AsyncSession); keeps sqlalchemy out of services
from app.fleet import fleet_text as T, repository as repo
from app.fleet.adapters.registry import active_source
from app.fleet.schemas import (
    AlertStrip,
    DefectOut,
    DefectRef,
    DocRef,
    DocumentOut,
    FleetList,
    InspectionOut,
    MaintenanceOut,
    MaintRef,
    NextDocExpiry,
    RecentRun,
    TruckDetail,
    TruckKpi,
    TruckOut,
    TruckRow,
)

LIST_WINDOW_DAYS = 30
DETAIL_WINDOW_DAYS = 90
RECENT_RUNS = 20


def _now() -> datetime:
    return datetime.now(UTC)


def _ratio(a: float, b: float) -> float | None:
    return round(a / b, 2) if b else None


def _home(u) -> str | None:
    return f"{u.home_base_city}, {u.home_base_state}" if u.home_base_city else None


async def list_trucks(session: AsyncSession, tenant_id: str) -> FleetList:
    now = _now()
    units = await repo.list_units(session, tenant_id)
    totals = await repo.run_totals(session, tenant_id, now - timedelta(days=LIST_WINDOW_DAYS))
    defects = await repo.defect_counts(session, tenant_id)
    expiries = await repo.next_doc_expiries(session, tenant_id)
    today = now.date()

    def row(u) -> TruckRow:
        t = totals.get(u.id, {})
        d = defects.get(u.id, {})
        nxt = expiries.get(u.id)
        return TruckRow(
            id=u.id, kind=u.kind, unit_number=u.unit_number, make=u.make, model=u.model, year=u.year,
            equipment=u.equipment, status=u.status, driver_name=u.assigned_driver_name,
            odometer_miles=u.odometer_miles, home_base=_home(u),
            runs_30d=int(t.get("runs", 0)), miles_30d=int(t.get("miles", 0)),
            revenue_30d_usd=round(t.get("revenue", 0.0), 2),
            cost_per_mile_30d_usd=_ratio(t.get("cost", 0.0), t.get("miles", 0)),
            next_doc_expiry=NextDocExpiry(kind=nxt[0], expires_on=nxt[1], days_left=(nxt[1] - today).days) if nxt else None,
            open_defects=d.get("open", 0), open_critical_defects=d.get("critical", 0),
        )

    rows = [row(u) for u in units]
    return FleetList(
        source=active_source(session).name,
        trucks=[r for r in rows if r.kind == "truck"],
        trailers=[r for r in rows if r.kind == "trailer"],
    )


async def alerts(session: AsyncSession, tenant_id: str) -> AlertStrip:
    today = _now().date()
    docs = [
        DocRef(
            id=x["doc"].id, truck_id=x["doc"].truck_id, unit_number=x["unit_number"], kind=x["doc"].kind,
            expires_on=x["doc"].expires_on, days_left=(x["doc"].expires_on - today).days,
        )
        for x in await repo.expiring_documents(session, tenant_id, today + timedelta(days=T.DOC_WINDOW_DAYS))
    ]
    defects = [
        DefectRef(
            id=x["defect"].id, truck_id=x["defect"].truck_id, unit_number=x["unit_number"],
            severity=x["defect"].severity, title=x["defect"].title, reported_at=x["defect"].reported_at,
        )
        for x in await repo.open_critical_defects(session, tenant_id)
    ]
    jobs = [
        MaintRef(
            id=x["job"].id, truck_id=x["job"].truck_id, unit_number=x["unit_number"], kind=x["job"].kind,
            title=x["job"].title, scheduled_for=x["job"].scheduled_for,
            days_until=(x["job"].scheduled_for - today).days,
        )
        for x in await repo.maintenance_due(session, tenant_id, today + timedelta(days=T.MAINT_WINDOW_DAYS))
    ]
    return AlertStrip(
        expiring_docs=docs, critical_defects=defects, maintenance_due=jobs,
        statements=T.statements(docs, defects, jobs),
    )


async def truck_detail(session: AsyncSession, tenant_id: str, unit_id: int) -> TruckDetail | None:
    u = await repo.get_unit(session, tenant_id, unit_id)
    if u is None:
        return None
    now = _now()
    today = now.date()
    kids = await repo.children(session, tenant_id, unit_id)
    t = await repo.one_truck_totals(session, tenant_id, unit_id, now - timedelta(days=DETAIL_WINDOW_DAYS))
    cost = t["fuel"] + t["driver"] + t["other"]
    kpis = TruckKpi(
        window_days=DETAIL_WINDOW_DAYS, runs_count=int(t["runs"]), miles=int(t["miles"]),
        revenue_usd=round(t["revenue"], 2), cost_fuel_usd=round(t["fuel"], 2), cost_driver_usd=round(t["driver"], 2),
        cost_other_usd=round(t["other"], 2), margin_usd=round(t["revenue"] - cost, 2),
        dollar_per_mile=_ratio(t["revenue"], t["miles"]), cost_per_mile=_ratio(cost, t["miles"]),
    )
    runs = [
        RecentRun(
            id=r["id"], pickup_at=r["pickup_at"], origin=r["origin"], dest=r["dest"], broker_name=r["broker_name"],
            miles=r["miles"], revenue_usd=round(r["revenue"], 2), cost_usd=round(r["cost"], 2),
            margin_usd=round(r["revenue"] - r["cost"], 2), dollar_per_mile=_ratio(r["revenue"], r["miles"]),
            margin_pct=round((r["revenue"] - r["cost"]) / r["revenue"] * 100, 1) if r["revenue"] else None,
        )
        for r in await repo.recent_runs(session, tenant_id, unit_id, RECENT_RUNS)
    ]
    open_def = [d for d in kids["defects"] if d.status == "open"]
    return TruckDetail(
        source=active_source(session).name,
        truck=TruckOut(
            id=u.id, kind=u.kind, unit_number=u.unit_number, vin=u.vin, make=u.make, model=u.model, year=u.year,
            plate=u.plate, equipment=u.equipment, status=u.status, odometer_miles=u.odometer_miles,
            home_base_city=u.home_base_city, home_base_state=u.home_base_state, driver_name=u.assigned_driver_name,
            last_lat=None if u.last_lat is None else float(u.last_lat),
            last_lng=None if u.last_lng is None else float(u.last_lng), last_seen_at=u.last_seen_at,
        ),
        summary=T.unit_summary(u, kpis, len(open_def), sum(1 for d in open_def if d.severity == "critical")),
        kpis=kpis,
        inspections=[
            InspectionOut(
                id=i.id, inspected_at=i.inspected_at, inspector_name=i.inspector_name, result=i.result,
                odometer_at_inspection=i.odometer_at_inspection, notes=i.notes,
            )
            for i in kids["inspections"]
        ],
        defects=[
            DefectOut(
                id=d.id, inspection_id=d.inspection_id, severity=d.severity, title=d.title, description=d.description,
                status=d.status, reported_at=d.reported_at, resolved_at=d.resolved_at,
            )
            for d in kids["defects"]
        ],
        maintenance=[
            MaintenanceOut(
                id=m.id, kind=m.kind, title=m.title, scheduled_for=m.scheduled_for, completed_at=m.completed_at,
                odometer_at=m.odometer_at, cost_usd=None if m.cost_usd is None else float(m.cost_usd), notes=m.notes,
            )
            for m in kids["maintenance"]
        ],
        documents=[
            DocumentOut(
                id=d.id, kind=d.kind, number=d.number, issued_on=d.issued_on, expires_on=d.expires_on,
                days_left=(d.expires_on - today).days,
            )
            for d in kids["documents"]
        ],
        recent_runs=runs,
    )
