"""lane_insights_cache — cached AI lane insights per (tenant, period_key)

Revision ID: 0035
Revises: 0034
Create Date: 2026-10-09

Same shape and RLS convention as ``lead_ai_summaries`` (0030): only
successful AI output is stored; ``input_hash`` invalidates on new data.
``entity_insights`` carries the per-state / per-lane suggestions the map
popover reads, so hovering never triggers a model call.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_T = "lane_insights_cache"
_JSON = postgresql.JSONB().with_variant(sa.JSON(), "sqlite")


def upgrade() -> None:
    op.create_table(
        _T,
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(26), nullable=False,
                  server_default="01LJMORGLJM00000000000000A"),
        sa.Column("period_key", sa.String(160), nullable=False),
        sa.Column("focus_lanes", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("declining_lanes", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("market_shifts", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("cost_levers", _JSON, nullable=False, server_default=sa.text("'[]'")),
        sa.Column("entity_insights", _JSON, nullable=False, server_default=sa.text("'{}'")),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "period_key", name="uq_lane_insights_tenant_period"),
    )
    op.create_index("ix_lane_insights_cache_tenant_id", _T, ["tenant_id"])
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
    op.drop_index("ix_lane_insights_cache_tenant_id", table_name=_T)
    op.drop_table(_T)
