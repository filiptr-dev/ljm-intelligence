"""freight_runs — completed-trip history for the lanes analysis page

Revision ID: 0034
Revises: 0033
Create Date: 2026-10-09

Separate from the live ``loads`` board on purpose: archival runs must not
pollute load dedupe / status / broker linking. Tenant-scoped + RLS, same
convention as 0018 / 0030. Margin and $-per-mile are derived in SQL only.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0034"
down_revision: str | None = "0033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "freight_runs"


def upgrade() -> None:
    op.create_table(
        _T,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(26), nullable=False,
                  server_default="01LJMORGLJM00000000000000A"),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("broker_name", sa.String(255), nullable=True),
        sa.Column("broker_lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="SET NULL"), nullable=True),
        sa.Column("origin_city", sa.String(128), nullable=False),
        sa.Column("origin_state", sa.String(8), nullable=False),
        sa.Column("dest_city", sa.String(128), nullable=False),
        sa.Column("dest_state", sa.String(8), nullable=False),
        sa.Column("origin_lat", sa.Numeric(8, 5), nullable=False),
        sa.Column("origin_lng", sa.Numeric(9, 5), nullable=False),
        sa.Column("dest_lat", sa.Numeric(8, 5), nullable=False),
        sa.Column("dest_lng", sa.Numeric(9, 5), nullable=False),
        sa.Column("miles", sa.Integer(), nullable=False),
        sa.Column("deadhead_miles", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("equipment", sa.String(32), nullable=True),
        sa.Column("pickup_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("delivery_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("revenue_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_fuel_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_driver_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_load_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("cost_dispatch_usd", sa.Numeric(10, 2), nullable=False),
        sa.Column("raw", postgresql.JSONB().with_variant(sa.JSON(), "sqlite"),
                  nullable=False, server_default=sa.text("'{}'")),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_freight_runs_tenant_id", _T, ["tenant_id"])
    op.create_index("ix_freight_runs_tenant_pickup", _T, ["tenant_id", "pickup_at"])
    op.create_index("ix_freight_runs_tenant_states", _T, ["tenant_id", "origin_state", "dest_state"])
    op.create_index("ix_freight_runs_tenant_source", _T, ["tenant_id", "source"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(f"ALTER TABLE {_T} ENABLE ROW LEVEL SECURITY")
        bind.exec_driver_sql(
            f"""
            CREATE POLICY {_T}_tenant_isolation ON {_T}
            USING (
                tenant_id = current_setting('app.tenant_id', true)
                OR coalesce(current_setting('app.tenant_id', true), '') = ''
            )
            WITH CHECK (
                tenant_id = current_setting('app.tenant_id', true)
                OR coalesce(current_setting('app.tenant_id', true), '') = ''
            )
            """
        )


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        bind.exec_driver_sql(f"DROP POLICY IF EXISTS {_T}_tenant_isolation ON {_T}")
    op.drop_index("ix_freight_runs_tenant_source", table_name=_T)
    op.drop_index("ix_freight_runs_tenant_states", table_name=_T)
    op.drop_index("ix_freight_runs_tenant_pickup", table_name=_T)
    op.drop_index("ix_freight_runs_tenant_id", table_name=_T)
    op.drop_table(_T)
