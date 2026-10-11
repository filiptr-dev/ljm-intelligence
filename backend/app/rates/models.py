"""Rates — ORM models.

Only one new table (migration 0031): `diesel_prices`. Public EIA dataset,
shared across tenants (same pattern as `fmcsa_snapshot_cache`), so NO
`tenant_id` column and NO RLS. All other reads join existing tables
(``leads``, ``loads``, ``sent_log``) through `repository.py`.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Date, Integer, Numeric, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.db import Base
from app.shared.orm import JSONType, TenantMixin


class DieselPrice(Base):
    __tablename__ = "diesel_prices"

    padd: Mapped[str] = mapped_column(String(8), primary_key=True)
    week_of: Mapped[date] = mapped_column(Date, primary_key=True)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )


class LaneRate(TenantMixin, Base):
    """Monthly lane rate card from LoadStop ``globalLaneRates`` (migration 0046)."""

    __tablename__ = "lane_rates"

    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer(), "sqlite"), primary_key=True, autoincrement=True
    )
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(128), nullable=False)
    origin_state: Mapped[str | None] = mapped_column(String(8))
    origin_city: Mapped[str | None] = mapped_column(String(128))
    dest_state: Mapped[str | None] = mapped_column(String(8))
    dest_city: Mapped[str | None] = mapped_column(String(128))
    equipment: Mapped[str | None] = mapped_column(String(32))
    as_of_month: Mapped[str | None] = mapped_column(String(7))
    low: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    avg: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    high: Mapped[Decimal | None] = mapped_column(Numeric(10, 2))
    miles: Mapped[int | None] = mapped_column(Integer)
    raw: Mapped[dict | None] = mapped_column(JSONType)
    ingested_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "source", "source_ref", name="lane_rates_tenant_source_ref_key"),
    )
