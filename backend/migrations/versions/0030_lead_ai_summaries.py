"""lead_ai_summaries — cached AI relationship summary per broker

Revision ID: 0030
Revises: 0029
Create Date: 2026-10-08

Broker AI summary plan. One row per (tenant, lead); only successful AI output
is written. RLS follows the 0018 convention.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0030"
down_revision: str | None = "0029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lead_ai_summaries",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(26), nullable=False,
                  server_default="01LJMORGLJM00000000000000A"),
        sa.Column("lead_id", sa.String(64), sa.ForeignKey("leads.id", ondelete="CASCADE"), nullable=False),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("input_hash", sa.String(64), nullable=False),
        sa.Column("model", sa.String(64), nullable=False, server_default=""),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "lead_id", name="uq_lead_ai_summaries_tenant_lead"),
    )
    op.create_index("ix_lead_ai_summaries_tenant_id", "lead_ai_summaries", ["tenant_id"])
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        t = "lead_ai_summaries"
        bind.exec_driver_sql(f"ALTER TABLE {t} ENABLE ROW LEVEL SECURITY")
        bind.exec_driver_sql(
            f"""
            CREATE POLICY {t}_tenant_isolation ON {t}
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
        bind.exec_driver_sql("DROP POLICY IF EXISTS lead_ai_summaries_tenant_isolation ON lead_ai_summaries")
    op.drop_index("ix_lead_ai_summaries_tenant_id", table_name="lead_ai_summaries")
    op.drop_table("lead_ai_summaries")
