"""followup_notes — one note + next_touch per (tenant, lead)

Revision ID: 0032
Revises: 0031
Create Date: 2026-10-09

Follow-ups kanban (Track B). Stage is derived from the underlying tables
(``sent_log``, ``call_outcomes``, ``mail_messages``, ``no_reply_tracker``);
only the dispatcher's running thought is stored here. One row per
(tenant, lead) keeps the write path tiny — history already lives in the
underlying event tables.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0032"
# NOTE: Track A's 0031 is not on this branch yet; re-point to "0031" at merge.
down_revision: str | None = "0031"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "followup_notes",
        sa.Column(
            "tenant_id",
            sa.String(26),
            nullable=False,
            server_default="01LJMORGLJM00000000000000A",
        ),
        sa.Column(
            "lead_id",
            sa.String(64),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("note", sa.Text(), nullable=False, server_default=""),
        sa.Column("next_touch", sa.Date(), nullable=True),
        sa.Column(
            "updated_at",
            sa.TIMESTAMP(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.PrimaryKeyConstraint("tenant_id", "lead_id", name="pk_followup_notes"),
    )
    op.create_index(
        "followup_notes_next_touch_idx",
        "followup_notes",
        ["tenant_id", "next_touch"],
    )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        t = "followup_notes"
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
        bind.exec_driver_sql(
            "DROP POLICY IF EXISTS followup_notes_tenant_isolation ON followup_notes"
        )
    op.drop_index("followup_notes_next_touch_idx", table_name="followup_notes")
    op.drop_table("followup_notes")
