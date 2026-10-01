"""loads table — live loads board (plan 2026-10-01)

Revision ID: 0010
Revises: 0009
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb():
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "loads",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("source", sa.String(32), nullable=False),
        sa.Column("source_ref", sa.String(128), nullable=False),
        sa.Column("broker_name", sa.String(255), nullable=False, server_default=sa.text("''")),
        sa.Column("broker_email", sa.String(320)),
        sa.Column("broker_phone", sa.String(64)),
        sa.Column("origin_city", sa.String(128)),
        sa.Column("origin_state", sa.String(8)),
        sa.Column("dest_city", sa.String(128)),
        sa.Column("dest_state", sa.String(8)),
        sa.Column("pickup_date", sa.DateTime(timezone=True)),
        sa.Column("equipment", sa.String(64)),
        sa.Column("rate_usd", sa.Numeric(10, 2)),
        sa.Column("miles", sa.Integer()),
        sa.Column("posted_at", sa.DateTime(timezone=True)),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("raw", _jsonb()),
    )
    op.create_index("loads_source_idx", "loads", ["source"])
    op.create_index("loads_source_ref_uq", "loads", ["source", "source_ref"], unique=True)
    op.create_index("loads_pickup_idx", "loads", ["pickup_date"])


def downgrade() -> None:
    op.drop_index("loads_pickup_idx", table_name="loads")
    op.drop_index("loads_source_ref_uq", table_name="loads")
    op.drop_index("loads_source_idx", table_name="loads")
    op.drop_table("loads")
