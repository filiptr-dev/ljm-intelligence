"""Rates — ORM models.

Only one new table (migration 0031): `diesel_prices`. Public EIA dataset,
shared across tenants (same pattern as `fmcsa_snapshot_cache`), so NO
`tenant_id` column and NO RLS. All other reads join existing tables
(``leads``, ``loads``, ``sent_log``) through `repository.py`.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, Numeric, String, func
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TIMESTAMP

from app.db import Base


class DieselPrice(Base):
    __tablename__ = "diesel_prices"

    padd: Mapped[str] = mapped_column(String(8), primary_key=True)
    week_of: Mapped[date] = mapped_column(Date, primary_key=True)
    price_usd: Mapped[Decimal] = mapped_column(Numeric(6, 3), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        TIMESTAMP(timezone=True), nullable=False, server_default=func.now()
    )
