"""fleet — the SQL seam. Every query for the fleet pages lives here.

Services never import ``select`` / ``func`` (import-linter contract); they call
these functions, which take a tenant id and return ORM rows or plain dicts.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import FreightRun as R
from app.fleet.models import Truck, TruckDefect, TruckDocument, TruckInspection, TruckMaintenance

_COST = R.cost_fuel_usd + R.cost_driver_usd + R.cost_load_usd + R.cost_dispatch_usd


def _f(v: Any) -> float:
    return float(v or 0)


async def list_units(session: AsyncSession, tenant_id: str) -> list[Truck]:
    q = select(Truck).where(Truck.tenant_id == tenant_id).order_by(Truck.kind.desc(), Truck.unit_number)
    return list((await session.execute(q)).scalars())


async def get_unit(session: AsyncSession, tenant_id: str, unit_id: int) -> Truck | None:
    q = select(Truck).where(Truck.tenant_id == tenant_id, Truck.id == unit_id)
    return (await session.execute(q)).scalar_one_or_none()


async def run_totals(session: AsyncSession, tenant_id: str, since: datetime) -> dict[int, dict[str, float]]:
    """Per-truck run aggregates for runs picked up at/after ``since`` (one GROUP BY)."""
    q = (
        select(
            R.truck_id,
            func.count().label("runs"),
            func.coalesce(func.sum(R.miles), 0).label("miles"),
            func.coalesce(func.sum(R.revenue_usd), 0).label("revenue"),
            func.coalesce(func.sum(_COST), 0).label("cost"),
        )
        .where(R.tenant_id == tenant_id, R.truck_id.is_not(None), R.pickup_at >= since)
        .group_by(R.truck_id)
    )
    return {
        r.truck_id: {"runs": int(r.runs), "miles": int(r.miles), "revenue": _f(r.revenue), "cost": _f(r.cost)}
        for r in (await session.execute(q)).all()
    }


async def one_truck_totals(session: AsyncSession, tenant_id: str, unit_id: int, since: datetime) -> dict[str, float]:
    q = select(
        func.count().label("runs"),
        func.coalesce(func.sum(R.miles), 0).label("miles"),
        func.coalesce(func.sum(R.revenue_usd), 0).label("revenue"),
        func.coalesce(func.sum(R.cost_fuel_usd), 0).label("fuel"),
        func.coalesce(func.sum(R.cost_driver_usd), 0).label("driver"),
        func.coalesce(func.sum(R.cost_load_usd + R.cost_dispatch_usd), 0).label("other"),
    ).where(R.tenant_id == tenant_id, R.truck_id == unit_id, R.pickup_at >= since)
    r = (await session.execute(q)).one()
    return {
        "runs": int(r.runs), "miles": int(r.miles), "revenue": _f(r.revenue),
        "fuel": _f(r.fuel), "driver": _f(r.driver), "other": _f(r.other),
    }


async def defect_counts(session: AsyncSession, tenant_id: str) -> dict[int, dict[str, int]]:
    """Open defects per unit: ``{"open": n, "critical": n}``."""
    q = (
        select(
            TruckDefect.truck_id,
            func.count().label("open"),
            func.coalesce(func.sum(case((TruckDefect.severity == "critical", 1), else_=0)), 0).label("critical"),
        )
        .where(TruckDefect.tenant_id == tenant_id, TruckDefect.status == "open")
        .group_by(TruckDefect.truck_id)
    )
    return {r.truck_id: {"open": int(r.open), "critical": int(r.critical)} for r in (await session.execute(q)).all()}


async def next_doc_expiries(session: AsyncSession, tenant_id: str) -> dict[int, tuple[str, date]]:
    """The soonest-expiring document per unit."""
    q = select(TruckDocument.truck_id, TruckDocument.kind, TruckDocument.expires_on).where(
        TruckDocument.tenant_id == tenant_id
    ).order_by(TruckDocument.expires_on.desc())
    out: dict[int, tuple[str, date]] = {}
    for r in (await session.execute(q)).all():
        out[r.truck_id] = (r.kind, r.expires_on)  # later rows are sooner, so the soonest wins
    return out


async def children(session: AsyncSession, tenant_id: str, unit_id: int) -> dict[str, list[Any]]:
    async def many(model: Any, *order: Any) -> list[Any]:
        q = select(model).where(model.tenant_id == tenant_id, model.truck_id == unit_id).order_by(*order)
        return list((await session.execute(q)).scalars())

    return {
        "inspections": await many(TruckInspection, TruckInspection.inspected_at.desc()),
        "defects": await many(TruckDefect, TruckDefect.reported_at.desc()),
        "maintenance": await many(TruckMaintenance, TruckMaintenance.scheduled_for.desc().nulls_last()),
        "documents": await many(TruckDocument, TruckDocument.expires_on),
    }


async def recent_runs(session: AsyncSession, tenant_id: str, unit_id: int, limit: int) -> list[dict[str, Any]]:
    q = (
        select(R, _COST.label("cost"))
        .where(R.tenant_id == tenant_id, R.truck_id == unit_id)
        .order_by(R.pickup_at.desc(), R.id.desc())
        .limit(limit)
    )
    return [
        {
            "id": r.id, "pickup_at": r.pickup_at, "origin": f"{r.origin_city}, {r.origin_state}",
            "dest": f"{r.dest_city}, {r.dest_state}", "broker_name": r.broker_name, "miles": r.miles,
            "revenue": _f(r.revenue_usd), "cost": _f(cost),
        }
        for r, cost in (await session.execute(q)).all()
    ]


async def expiring_documents(session: AsyncSession, tenant_id: str, until: date) -> list[dict[str, Any]]:
    q = (
        select(TruckDocument, Truck.unit_number)
        .join(Truck, Truck.id == TruckDocument.truck_id)
        .where(TruckDocument.tenant_id == tenant_id, TruckDocument.expires_on <= until)
        .order_by(TruckDocument.expires_on, Truck.unit_number)
    )
    return [{"doc": d, "unit_number": un} for d, un in (await session.execute(q)).all()]


async def open_critical_defects(session: AsyncSession, tenant_id: str) -> list[dict[str, Any]]:
    q = (
        select(TruckDefect, Truck.unit_number)
        .join(Truck, Truck.id == TruckDefect.truck_id)
        .where(TruckDefect.tenant_id == tenant_id, TruckDefect.status == "open", TruckDefect.severity == "critical")
        .order_by(TruckDefect.reported_at.desc(), Truck.unit_number)
    )
    return [{"defect": d, "unit_number": un} for d, un in (await session.execute(q)).all()]


async def maintenance_due(session: AsyncSession, tenant_id: str, until: date) -> list[dict[str, Any]]:
    q = (
        select(TruckMaintenance, Truck.unit_number)
        .join(Truck, Truck.id == TruckMaintenance.truck_id)
        .where(
            TruckMaintenance.tenant_id == tenant_id,
            TruckMaintenance.completed_at.is_(None),
            TruckMaintenance.scheduled_for.is_not(None),
            TruckMaintenance.scheduled_for <= until,
        )
        .order_by(TruckMaintenance.scheduled_for, Truck.unit_number)
    )
    return [{"job": m, "unit_number": un} for m, un in (await session.execute(q)).all()]


async def snapshot_rows(session: AsyncSession, tenant_id: str, source: str) -> dict[str, list[Any]]:
    """Every row of one ``source`` for the tenant, for ``FleetSource.snapshot``."""

    async def of(model: Any) -> list[Any]:
        q = select(model).where(model.tenant_id == tenant_id, model.source == source).order_by(model.id)
        return list((await session.execute(q)).scalars())

    return {
        "units": await of(Truck), "inspections": await of(TruckInspection), "defects": await of(TruckDefect),
        "maintenance": await of(TruckMaintenance), "documents": await of(TruckDocument),
    }


async def unit_number_taken(session: AsyncSession, tenant_id: str, unit_number: str, except_id: int | None = None) -> bool:
    q = select(Truck.id).where(Truck.tenant_id == tenant_id, Truck.unit_number == unit_number)
    if except_id is not None:
        q = q.where(Truck.id != except_id)
    return (await session.execute(q.limit(1))).first() is not None


def add_unit(session: AsyncSession, tenant_id: str, fields: dict[str, Any]) -> Truck:
    """Stage a new owner-entered unit (``source='manual'``); the caller commits."""
    unit = Truck(tenant_id=tenant_id, source="manual", **fields)
    session.add(unit)
    return unit


async def delete_unit(session: AsyncSession, tenant_id: str, unit_id: int) -> bool:
    """Children cascade (FK ON DELETE CASCADE); runs keep their row with ``truck_id`` set NULL."""
    res = await session.execute(delete(Truck).where(Truck.tenant_id == tenant_id, Truck.id == unit_id))
    return (res.rowcount or 0) > 0


async def commit_unique(session: AsyncSession) -> bool:
    """Commit; ``False`` (after a rollback) when the unique (tenant, unit_number) constraint refused it."""
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True
