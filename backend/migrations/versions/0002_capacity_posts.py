"""capacity_posts — additive slice for /capacity tool

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "capacity_posts",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("equipment", sa.String(64), nullable=False),
        sa.Column("origin_city", sa.String(128)),
        sa.Column("origin_state", sa.String(2), nullable=False),
        sa.Column("origin_lat", sa.Float()),
        sa.Column("origin_lng", sa.Float()),
        sa.Column("destinations", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
        sa.Column("available_date", sa.String(32)),
        sa.Column("dest_city", sa.String(128)),
        sa.Column("dest_state", sa.String(2)),
        sa.Column("pickup_date", sa.String(32)),
        sa.Column("weight_lbs", sa.Integer),
        sa.Column("rate_usd", sa.Integer),
        sa.Column("notes", sa.Text),
        sa.Column("status", sa.String(16), server_default=sa.text("'open'"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("capacity_posts_kind_status", "capacity_posts", ["kind", "status"])


def downgrade() -> None:
    op.drop_index("capacity_posts_kind_status", table_name="capacity_posts")
    op.drop_table("capacity_posts")
