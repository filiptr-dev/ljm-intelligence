"""Fleet HTTP surface — thin: parse, call the service, return.

GET /fleet/trucks       trucks + trailers with 30-day KPIs, next expiring document, open defects
GET /fleet/trucks/{id}  one unit: inspections, defects, maintenance, documents, 90-day KPIs, last 20 runs
GET /fleet/alerts       the alerts strip: expiring documents, open critical defects, maintenance due
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path

from app.db import Session
from app.fleet import service as svc
from app.fleet.schemas import AlertStrip, FleetList, TruckDetail
from app.shared.tenant import current_tenant

router = APIRouter(prefix="/fleet", tags=["fleet"])


@router.get("/trucks", response_model=FleetList)
async def fleet_trucks(session: Session) -> FleetList:
    return await svc.list_trucks(session, current_tenant())


@router.get("/trucks/{unit_id}", response_model=TruckDetail)
async def fleet_truck_detail(session: Session, unit_id: int = Path(ge=1)) -> TruckDetail:
    detail = await svc.truck_detail(session, current_tenant(), unit_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="unit not found")
    return detail


@router.get("/alerts", response_model=AlertStrip)
async def fleet_alerts(session: Session) -> AlertStrip:
    return await svc.alerts(session, current_tenant())
