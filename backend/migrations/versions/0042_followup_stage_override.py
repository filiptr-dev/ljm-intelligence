"""followup_notes: stage_override + stage_override_at

Revision ID: 0042
Revises: 0041
Create Date: 2026-10-09

Manual pipeline-stage override for the Follow-ups board. Both columns
nullable (an unset override = no override). ``stage_override`` is a short
enum-ish string with a CHECK constraint so bad values never land, which
means the service layer can trust whatever the DB returned.

``stage_override_at`` is ``timestamptz`` — the board compares it to each
card's derived ``last_activity_at``, and timezone-aware math is the only
math that is safe here.

Why columns on ``followup_notes`` rather than a new table: that row is
already one per (tenant, lead) with RLS; adding two nullable columns
costs nothing and inherits the existing isolation policy. The empty-string
server default on the parent table (migration 0032) means the upsert path
for notes that lives in ``repository.py`` keeps working untouched — this
migration only widens the row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0042"
down_revision: str | None = "0041"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_ALLOWED_STAGES = ("new", "contacted", "replied", "booked")


def upgrade() -> None:
    with op.batch_alter_table("followup_notes") as b:
        b.add_column(sa.Column("stage_override", sa.String(16), nullable=True))
        b.add_column(
            sa.Column(
                "stage_override_at",
                sa.TIMESTAMP(timezone=True),
                nullable=True,
            )
        )
        b.create_check_constraint(
            "ck_followup_notes_stage_override",
            "stage_override IS NULL OR stage_override IN "
            "('new','contacted','replied','booked')",
        )


def downgrade() -> None:
    with op.batch_alter_table("followup_notes") as b:
        b.drop_constraint("ck_followup_notes_stage_override", type_="check")
        b.drop_column("stage_override_at")
        b.drop_column("stage_override")
