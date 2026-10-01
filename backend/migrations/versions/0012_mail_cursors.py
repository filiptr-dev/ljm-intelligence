"""mail_cursors table — Gmail history-id checkpoints per mailbox

Revision ID: 0012
Revises: 0011
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_cursors",
        sa.Column("mailbox", sa.String(255), primary_key=True),
        sa.Column("history_id", sa.String(64), nullable=False),
        sa.Column("backfilled_through_at", sa.DateTime(timezone=True)),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("mail_cursors")
