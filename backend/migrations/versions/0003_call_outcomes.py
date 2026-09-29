"""call_outcomes — additive slice for /tools/call-list (Slice 1)

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-29

Additive only: one new table + two indexes. No changes to shipped tables.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "call_outcomes",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "lead_id",
            sa.String(64),
            sa.ForeignKey("leads.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # outcome ∈ {booked, callback, not_interested, no_answer} — enforced app-side + CHECK.
        sa.Column("outcome", sa.String(32), nullable=False),
        sa.Column("callback_at", sa.Date(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "logged_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("logged_by", sa.String(128), nullable=True),
        sa.CheckConstraint(
            "outcome IN ('booked','callback','not_interested','no_answer')",
            name="call_outcomes_outcome_check",
        ),
    )
    # Hot lookup: latest outcome per lead.
    op.create_index(
        "call_outcomes_lead_id_logged_at",
        "call_outcomes",
        ["lead_id", sa.text("logged_at DESC")],
    )
    # Partial index: only scheduled callbacks — small, fast pin lookup.
    op.create_index(
        "call_outcomes_callback_at",
        "call_outcomes",
        ["callback_at"],
        postgresql_where=sa.text("outcome = 'callback'"),
    )


def downgrade() -> None:
    op.drop_index("call_outcomes_callback_at", table_name="call_outcomes")
    op.drop_index("call_outcomes_lead_id_logged_at", table_name="call_outcomes")
    op.drop_table("call_outcomes")
