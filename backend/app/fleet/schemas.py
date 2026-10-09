"""fleet — response DTOs. Fields with defaults are still always sent, so the
generated TypeScript types mark them required instead of optional."""

from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.analysis.operating_area import OPERATING_STATE_SET


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


# --- owner-managed units (POST / PATCH) ---------------------------------------------------------

Kind = Literal["truck", "trailer"]
Equipment = Literal["van", "reefer", "flatbed", "stepdeck"]
Status = Literal["available", "on_load", "in_shop", "out_of_service"]


class _TruckFields(BaseModel):
    """Shared validation. Blank strings become ``None``; the state must be one we operate in."""

    model_config = ConfigDict(str_strip_whitespace=True, extra="forbid")

    make: str | None = Field(default=None, max_length=64)
    model: str | None = Field(default=None, max_length=64)
    year: int | None = Field(default=None, ge=1980, le=2100)
    vin: str | None = Field(default=None, max_length=32)
    plate: str | None = Field(default=None, max_length=24)
    equipment: Equipment | None = None
    odometer_miles: int | None = Field(default=None, ge=0, le=5_000_000)
    home_base_city: str | None = Field(default=None, max_length=128)
    home_base_state: str | None = Field(default=None, max_length=8)
    assigned_driver_name: str | None = Field(default=None, max_length=128)

    @field_validator("make", "model", "vin", "plate", "home_base_city", "assigned_driver_name", mode="after")
    @classmethod
    def _blank_is_none(cls, v: str | None) -> str | None:
        return v or None

    @field_validator("home_base_state", mode="after")
    @classmethod
    def _state_in_area(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.upper()
        if v not in OPERATING_STATE_SET:
            raise ValueError("home base state must be one of the states you operate in")
        return v


class TruckCreate(_TruckFields):
    unit_number: str = Field(min_length=1, max_length=32)
    kind: Kind = "truck"
    status: Status = "available"


class TruckUpdate(_TruckFields):
    """Partial update: only the fields the client sent are changed (``model_fields_set``)."""

    unit_number: str | None = Field(default=None, min_length=1, max_length=32)
    kind: Kind | None = None
    status: Status | None = None

    @field_validator("unit_number", "kind", "status", mode="after")
    @classmethod
    def _not_null(cls, v: str | None) -> str | None:
        if v is None:
            raise ValueError("cannot be cleared")
        return v
