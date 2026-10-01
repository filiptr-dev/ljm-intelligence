"""sent_log provider fields + settings.mail_sender_override

Revision ID: 0013
Revises: 0012
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("sent_log") as b:
        b.add_column(sa.Column("provider_message_id", sa.String(255)))
        b.add_column(sa.Column("thread_id", sa.String(255)))
        b.add_column(sa.Column("in_reply_to", sa.String(255)))
        b.add_column(sa.Column("is_test", sa.Boolean(), nullable=False, server_default=sa.false()))
    op.create_index("sent_log_thread_idx", "sent_log", ["thread_id"])
    with op.batch_alter_table("settings") as b:
        b.add_column(sa.Column("mail_sender_override", sa.String(16)))


def downgrade() -> None:
    with op.batch_alter_table("settings") as b:
        b.drop_column("mail_sender_override")
    op.drop_index("sent_log_thread_idx", table_name="sent_log")
    with op.batch_alter_table("sent_log") as b:
        b.drop_column("is_test")
        b.drop_column("in_reply_to")
        b.drop_column("thread_id")
        b.drop_column("provider_message_id")
