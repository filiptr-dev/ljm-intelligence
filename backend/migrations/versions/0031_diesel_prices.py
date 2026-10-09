"""diesel_prices — weekly EIA diesel $ per PADD region.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-09

Lane-rate engine data (plan 2026-10-09-tools-rates-profit-backhaul). Public
EIA dataset — shared across tenants (same pattern as fmcsa_snapshot_cache),
so no `tenant_id` column, no RLS. Primary key is `(padd, week_of)` so a
weekly backfill upsert is idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "diesel_prices",
        sa.Column("padd", sa.String(8), nullable=False),  # '1A','1B','1C','2','3','4','5'
        sa.Column("week_of", sa.Date(), nullable=False),  # Monday of the EIA week
        sa.Column("price_usd", sa.Numeric(6, 3), nullable=False),
        sa.Column(
            "fetched_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("padd", "week_of", name="diesel_prices_pkey"),
    )
    op.create_index(
        "diesel_prices_padd_recent",
        "diesel_prices",
        ["padd", sa.text("week_of DESC")],
    )


def downgrade() -> None:
    op.drop_index("diesel_prices_padd_recent", table_name="diesel_prices")
    op.drop_table("diesel_prices")
