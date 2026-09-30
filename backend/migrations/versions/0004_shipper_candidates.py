"""shipper_candidates — additive slice for /tools/shipper-finder (Slice 1)

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-30

Additive only: one new table + three indexes. No changes to shipped tables.

The table is the un-promoted discovery bucket for OSM finds and FMCSA shippers
that haven't been rolled into `leads` yet — persist-everything principle. When
the operator clicks "Add to leads", we set `promoted_lead_id` to the newly-created
or existing lead's id (never mutating `leads` schema).
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "shipper_candidates",
        # id shape: "FMCSA-MC-123456" | "FMCSA-DOT-98765" | "OSM-way-42" | "OSM-node-7"
        sa.Column("id", sa.String(96), primary_key=True),
        # source ∈ {FMCSA, OSM} — enforced app-side + CHECK.
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("state", sa.String(2), nullable=False),
        sa.Column("city", sa.String(128), nullable=True),
        sa.Column("address", sa.String(255), nullable=True),
        sa.Column("lat", sa.Float(), nullable=True),
        sa.Column("lng", sa.Float(), nullable=True),
        sa.Column("mc", sa.String(32), nullable=True),
        sa.Column("dot", sa.String(32), nullable=True),
        sa.Column("domain", sa.String(255), nullable=True),
        sa.Column("phone", sa.String(64), nullable=True),
        sa.Column("primary_email", sa.String(255), nullable=True),
        sa.Column("osm_tags", JSONB(), nullable=True),
        sa.Column("raw", JSONB(), nullable=True),
        sa.Column("evidence", JSONB(), nullable=True),
        # FK-ish (soft) — leads.id may not exist yet at insert time for un-promoted rows,
        # so we don't add a hard FK; the /promote endpoint sets this atomically.
        sa.Column("promoted_lead_id", sa.String(64), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "source IN ('FMCSA','OSM')",
            name="shipper_candidates_source_check",
        ),
    )
    # State filter is the hottest query — every list call filters on it.
    op.create_index("shipper_candidates_state", "shipper_candidates", ["state"])
    # Source toggle in the filter bar (FMCSA / OSM / Both).
    op.create_index("shipper_candidates_source", "shipper_candidates", ["source"])
    # Partial index: the un-promoted bucket is what the operator scans; keep it small + hot.
    op.create_index(
        "shipper_candidates_unpromoted",
        "shipper_candidates",
        ["promoted_lead_id"],
        postgresql_where=sa.text("promoted_lead_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("shipper_candidates_unpromoted", table_name="shipper_candidates")
    op.drop_index("shipper_candidates_source", table_name="shipper_candidates")
    op.drop_index("shipper_candidates_state", table_name="shipper_candidates")
    op.drop_table("shipper_candidates")
