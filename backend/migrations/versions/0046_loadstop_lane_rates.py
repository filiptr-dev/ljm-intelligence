"""LoadStop lane_rates — monthly lane rate cards from ``globalLaneRates``.

Revision ID: 0046
Revises: 0045
Create Date: 2026-10-11

A separate table (rather than widening ``loads``) because lane rate
cards are monthly aggregates — not posted loads with a rate — and the
Rates page wants to read them keyed on the lane/month tuple. The
``loads`` table stays what it is: a per-posting snapshot.

Unique on ``(tenant_id, source, source_ref)`` — the ABP record id makes
a repeat ingest an upsert, not a duplicate.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0046"
down_revision: str | None = "0045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lane_rates",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"), primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(length=26), nullable=False),
        sa.Column("source", sa.String(length=32), nullable=False),
        sa.Column("source_ref", sa.String(length=128), nullable=False),
        sa.Column("origin_state", sa.String(length=8), nullable=True),
        sa.Column("origin_city", sa.String(length=128), nullable=True),
        sa.Column("dest_state", sa.String(length=8), nullable=True),
        sa.Column("dest_city", sa.String(length=128), nullable=True),
        sa.Column("equipment", sa.String(length=32), nullable=True),
        sa.Column("as_of_month", sa.String(length=7), nullable=True),  # 'YYYY-MM'
        sa.Column("low", sa.Numeric(10, 2), nullable=True),
        sa.Column("avg", sa.Numeric(10, 2), nullable=True),
        sa.Column("high", sa.Numeric(10, 2), nullable=True),
        sa.Column("miles", sa.Integer(), nullable=True),
        sa.Column("raw", sa.JSON(), nullable=True),
        sa.Column("ingested_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("tenant_id", "source", "source_ref", name="lane_rates_tenant_source_ref_key"),
        sa.Index("ix_lane_rates_lane_month", "origin_state", "dest_state", "equipment", "as_of_month"),
    )


def downgrade() -> None:
    op.drop_table("lane_rates")
