"""lead_ai_summaries — AI risks + next step

Revision ID: 0033
Revises: 0032
Create Date: 2026-10-09

The broker AI panel shows the summary, risks and an AI-written recommended
next step. Both columns are nullable: a NULL ``risks`` marks a legacy row,
which the service treats as a cache miss and regenerates.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0033"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("lead_ai_summaries", sa.Column("risks", postgresql.JSONB(), nullable=True))
    op.add_column("lead_ai_summaries", sa.Column("next_step", postgresql.JSONB(), nullable=True))


def downgrade() -> None:
    op.drop_column("lead_ai_summaries", "next_step")
    op.drop_column("lead_ai_summaries", "risks")
