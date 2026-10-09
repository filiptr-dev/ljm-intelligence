"""Fleet HTTP surface — thin: parse, call the service, return.

GET /fleet/trucks       trucks + trailers with 30-day KPIs, next expiring document, open defects
GET /fleet/trucks/{id}  one unit: inspections, defects, maintenance, documents, 90-day KPIs, last 20 runs
GET /fleet/alerts       the alerts strip: expiring documents, open critical defects, maintenance due
POST /fleet/trucks      add a unit (owner-entered, source='manual'); 409 when the unit number is taken
PATCH /fleet/trucks/{id}   change the fields sent; DELETE removes the unit and its records
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Path, Response, status

from app.db import Session
from app.fleet import service as svc
from app.fleet.schemas import AlertStrip, FleetList, TruckCreate, TruckDetail, TruckUpdate
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


_TAKEN = HTTPException(status_code=status.HTTP_409_CONFLICT, detail="that unit number is already in your fleet")


@router.post("/trucks", response_model=TruckDetail, status_code=status.HTTP_201_CREATED)
async def fleet_truck_create(session: Session, body: TruckCreate) -> TruckDetail:
    try:
        return await svc.create_unit(session, current_tenant(), body)
    except svc.UnitNumberTaken:
        raise _TAKEN from None


@router.patch("/trucks/{unit_id}", response_model=TruckDetail)
async def fleet_truck_update(session: Session, body: TruckUpdate, unit_id: int = Path(ge=1)) -> TruckDetail:
    try:
        detail = await svc.update_unit(session, current_tenant(), unit_id, body)
    except svc.UnitNumberTaken:
        raise _TAKEN from None
    if detail is None:
        raise HTTPException(status_code=404, detail="unit not found")
    return detail


@router.delete("/trucks/{unit_id}", status_code=status.HTTP_204_NO_CONTENT)
async def fleet_truck_delete(session: Session, unit_id: int = Path(ge=1)) -> Response:
    if not await svc.delete_unit(session, current_tenant(), unit_id):
        raise HTTPException(status_code=404, detail="unit not found")
    return Response(status_code=status.HTTP_204_NO_CONTENT)
