"""fleet — response DTOs. Fields with defaults are still always sent, so the
generated TypeScript types mark them required instead of optional."""

from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class _Out(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)


class NextDocExpiry(_Out):
    kind: str
    expires_on: date
    days_left: int


class TruckRow(_Out):
    id: int
    kind: str
    unit_number: str
    make: str | None = None
    model: str | None = None
    year: int | None = None
    equipment: str | None = None
    status: str
    driver_name: str | None = None
    odometer_miles: int | None = None
    home_base: str | None = None
    runs_30d: int = 0
    miles_30d: int = 0
    revenue_30d_usd: float = 0.0
    cost_per_mile_30d_usd: float | None = None
    next_doc_expiry: NextDocExpiry | None = None
    open_defects: int = 0
    open_critical_defects: int = 0


class FleetList(_Out):
    source: str
    trucks: list[TruckRow]
    trailers: list[TruckRow]


class DocRef(_Out):
    id: int
    truck_id: int
    unit_number: str
    kind: str
    expires_on: date
    days_left: int


class DefectRef(_Out):
    id: int
    truck_id: int
    unit_number: str
    severity: str
    title: str
    reported_at: datetime


class MaintRef(_Out):
    id: int
    truck_id: int
    unit_number: str
    kind: str
    title: str | None = None
    scheduled_for: date
    days_until: int


class AlertStatement(_Out):
    key: str
    title: str
    text: str


class AlertStrip(_Out):
    expiring_docs: list[DocRef]
    critical_defects: list[DefectRef]
    maintenance_due: list[MaintRef]
    statements: list[AlertStatement]


class TruckOut(_Out):
    id: int
    kind: str
    unit_number: str
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    plate: str | None = None
    equipment: str | None = None
    status: str
    odometer_miles: int | None = None
    home_base_city: str | None = None
    home_base_state: str | None = None
    driver_name: str | None = None
    last_lat: float | None = None
    last_lng: float | None = None
    last_seen_at: datetime | None = None


class InspectionOut(_Out):
    id: int
    inspected_at: datetime
    inspector_name: str | None = None
    result: str
    odometer_at_inspection: int | None = None
    notes: str | None = None


class DefectOut(_Out):
    id: int
    inspection_id: int | None = None
    severity: str
    title: str
    description: str | None = None
    status: str
    reported_at: datetime
    resolved_at: datetime | None = None


class MaintenanceOut(_Out):
    id: int
    kind: str
    title: str | None = None
    scheduled_for: date | None = None
    completed_at: date | None = None
    odometer_at: int | None = None
    cost_usd: float | None = None
    notes: str | None = None


class DocumentOut(_Out):
    id: int
    kind: str
    number: str | None = None
    issued_on: date | None = None
    expires_on: date
    days_left: int


class TruckKpi(_Out):
    window_days: int
    runs_count: int = 0
    miles: int = 0
    revenue_usd: float = 0.0
    cost_fuel_usd: float = 0.0
    cost_driver_usd: float = 0.0
    cost_other_usd: float = 0.0
    margin_usd: float = 0.0
    dollar_per_mile: float | None = None
    cost_per_mile: float | None = None


class RecentRun(_Out):
    id: int
    pickup_at: datetime
    origin: str
    dest: str
    broker_name: str | None = None
    miles: int
    revenue_usd: float
    cost_usd: float
    margin_usd: float
    dollar_per_mile: float | None = None
    margin_pct: float | None = None


class TruckDetail(_Out):
    source: str
    truck: TruckOut
    summary: str
    kpis: TruckKpi
    inspections: list[InspectionOut]
    defects: list[DefectOut]
    maintenance: list[MaintenanceOut]
    documents: list[DocumentOut]
    recent_runs: list[RecentRun]
