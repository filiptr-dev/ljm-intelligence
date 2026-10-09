"""The FleetSource port — where fleet data comes from.

A ``Protocol`` (not a base class), same shape as ``LoadSource`` in
``integrations/adapters/loadboard/base.py``: the demo adapter and a future
adapter for the carrier's own fleet software sit side by side with no shared
ancestor, and swapping one for the other is one line in ``adapters/registry.py``.

The ``Raw*`` dataclasses are the vendor-neutral snapshot shape (like ``RawLoad``):
whatever a vendor sends, its adapter maps it to these. ``raw`` keeps the original
payload so nothing is lost in translation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Protocol


@dataclass
class RawUnit:
    source_ref: str
    unit_number: str
    kind: str = "truck"
    vin: str | None = None
    make: str | None = None
    model: str | None = None
    year: int | None = None
    plate: str | None = None
    equipment: str | None = None
    status: str = "available"
    odometer_miles: int | None = None
    home_base_city: str | None = None
    home_base_state: str | None = None
    assigned_driver_name: str | None = None
    last_lat: float | None = None
    last_lng: float | None = None
    last_seen_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RawInspection:
    unit_ref: str
    inspected_at: datetime
    result: str
    inspector_name: str | None = None
    odometer_at_inspection: int | None = None
    notes: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RawDefect:
    unit_ref: str
    severity: str
    title: str
    reported_at: datetime
    status: str = "open"
    description: str | None = None
    resolved_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RawMaintenance:
    unit_ref: str
    kind: str
    title: str | None = None
    scheduled_for: date | None = None
    completed_at: date | None = None
    odometer_at: int | None = None
    cost_usd: float | None = None
    notes: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class RawDocument:
    unit_ref: str
    kind: str
    expires_on: date
    number: str | None = None
    issued_on: date | None = None
    file_url: str | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class FleetSnapshot:
    source: str
    units: list[RawUnit] = field(default_factory=list)
    inspections: list[RawInspection] = field(default_factory=list)
    defects: list[RawDefect] = field(default_factory=list)
    maintenance: list[RawMaintenance] = field(default_factory=list)
    documents: list[RawDocument] = field(default_factory=list)


class FleetSource(Protocol):
    name: str
    enabled: bool

    async def snapshot(self, tenant_id: str) -> FleetSnapshot: ...
