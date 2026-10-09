"""fleet models — trucks/trailers and their Inspectio-style records (0038).

Every table is tenant-scoped (``TenantMixin``) and carries ``source`` (``demo``
today, a vendor slug later) plus ``raw`` so an arbitrary vendor payload survives
the round-trip. ``source='demo'`` rows are seeded by migration 0038 and purged
by its downgrade.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.shared.orm import JSONType, TenantMixin

_PK = BigInteger().with_variant(Integer(), "sqlite")


class Truck(TenantMixin, Base):
    """A power unit or a trailer (``kind``). The page lists ``kind='truck'`` first."""

    __tablename__ = "trucks"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), nullable=False, default="truck", server_default=text("'truck'"))
    unit_number: Mapped[str] = mapped_column(String(32), nullable=False)
    vin: Mapped[str | None] = mapped_column(String(32))
    make: Mapped[str | None] = mapped_column(String(64))
    model: Mapped[str | None] = mapped_column(String(64))
    year: Mapped[int | None] = mapped_column(Integer)
    plate: Mapped[str | None] = mapped_column(String(24))
    equipment: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(24), nullable=False, default="available")
    odometer_miles: Mapped[int | None] = mapped_column(Integer)
    home_base_city: Mapped[str | None] = mapped_column(String(128))
    home_base_state: Mapped[str | None] = mapped_column(String(8))
    assigned_driver_name: Mapped[str | None] = mapped_column(String(128))
    last_lat: Mapped[float | None] = mapped_column(Numeric(8, 5))
    last_lng: Mapped[float | None] = mapped_column(Numeric(9, 5))
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(String(64))
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_trucks_tenant_source", "tenant_id", "source"),)


class TruckInspection(TenantMixin, Base):
    __tablename__ = "truck_inspections"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    truck_id: Mapped[int] = mapped_column(ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False)
    inspected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    inspector_name: Mapped[str | None] = mapped_column(String(128))
    result: Mapped[str] = mapped_column(String(16), nullable=False)
    odometer_at_inspection: Mapped[int | None] = mapped_column(Integer)
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        Index("ix_truck_inspections_tenant_source", "tenant_id", "source"),
        Index("ix_truck_inspections_truck_at", "truck_id", "inspected_at"),
    )


class TruckDefect(TenantMixin, Base):
    __tablename__ = "truck_defects"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    truck_id: Mapped[int] = mapped_column(ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False)
    inspection_id: Mapped[int | None] = mapped_column(ForeignKey("truck_inspections.id", ondelete="SET NULL"))
    severity: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="open")
    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        Index("ix_truck_defects_tenant_source", "tenant_id", "source"),
        Index("ix_truck_defects_truck_status", "truck_id", "status"),
    )


class TruckMaintenance(TenantMixin, Base):
    __tablename__ = "truck_maintenance"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    truck_id: Mapped[int] = mapped_column(ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(16), nullable=False)
    title: Mapped[str | None] = mapped_column(String(255))
    scheduled_for: Mapped[date | None] = mapped_column(Date)
    completed_at: Mapped[date | None] = mapped_column(Date)
    odometer_at: Mapped[int | None] = mapped_column(Integer)
    cost_usd: Mapped[float | None] = mapped_column(Numeric(10, 2))
    notes: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        Index("ix_truck_maintenance_tenant_source", "tenant_id", "source"),
        Index("ix_truck_maintenance_truck_sched", "truck_id", "scheduled_for"),
    )


class TruckDocument(TenantMixin, Base):
    __tablename__ = "truck_documents"

    id: Mapped[int] = mapped_column(_PK, primary_key=True, autoincrement=True)
    truck_id: Mapped[int] = mapped_column(ForeignKey("trucks.id", ondelete="CASCADE"), nullable=False)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    number: Mapped[str | None] = mapped_column(String(64))
    issued_on: Mapped[date | None] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date, nullable=False)
    file_url: Mapped[str | None] = mapped_column(String(512))
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    raw: Mapped[dict] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))

    __table_args__ = (
        Index("ix_truck_documents_tenant_source", "tenant_id", "source"),
        Index("ix_truck_documents_expires_on", "expires_on"),
        Index("ix_truck_documents_truck", "truck_id"),
    )
