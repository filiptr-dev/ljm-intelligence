"""lead main lane — 4 nullable columns on `leads` for the Main lane card

Revision ID: 0021
Revises: 0020
Create Date: 2026-10-05

Restores the "Main lane" card the pre-ba15198 broker detail page carried.
The seeded page computed origin/destination/miles/last_seen from the demo
store; the real backend has no such columns on `leads` today. These four
nullable fields let the enrichment pass (or an operator fix-up) populate
the card without faking anything — the UI renders only when at least one
field is present. All nullable: existing rows read unchanged.

Columns (all nullable, additive):
  * lane_origin_region       — e.g. "Midwest" / "FL"
  * lane_destination_region  — e.g. "West Coast" / "NY"
  * lane_miles_band          — e.g. "500-1500" / "<500"
  * lane_last_seen_at        — when the lane was last observed in traffic
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_COLS = [
    ("lane_origin_region", sa.String(64)),
    ("lane_destination_region", sa.String(64)),
    ("lane_miles_band", sa.String(32)),
    ("lane_last_seen_at", sa.DateTime(timezone=True)),
]


def upgrade() -> None:
    for name, col_type in _COLS:
        op.add_column("leads", sa.Column(name, col_type, nullable=True))


def downgrade() -> None:
    for name, _ in _COLS:
        op.drop_column("leads", name)
