"""mail_messages table — Google Workspace mail connector ingest

Revision ID: 0011
Revises: 0010
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _jsonb():
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "mail_messages",
        sa.Column("mailbox", sa.String(255), primary_key=True),
        sa.Column("message_id", sa.String(255), primary_key=True),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("history_id", sa.String(64), nullable=False),
        sa.Column("from_addr", sa.String(320), nullable=False, server_default=sa.text("''")),
        sa.Column("to_addrs", _jsonb(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("cc_addrs", _jsonb(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("subject", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("in_reply_to", sa.String(255)),
        sa.Column("references_hdr", _jsonb(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("body_text", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("body_html", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("labels", _jsonb(), nullable=False, server_default=sa.text("'[]'")),
        sa.Column("ingested_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("raw", _jsonb(), nullable=False, server_default=sa.text("'{}'")),
    )
    op.create_index("mail_messages_thread_idx", "mail_messages", ["thread_id"])
    op.create_index("mail_messages_from_idx", "mail_messages", ["from_addr"])
    op.create_index("mail_messages_sent_at_idx", "mail_messages", ["sent_at"])
    op.create_index("mail_messages_mbx_hist_idx", "mail_messages", ["mailbox", "history_id"])


def downgrade() -> None:
    op.drop_index("mail_messages_mbx_hist_idx", table_name="mail_messages")
    op.drop_index("mail_messages_sent_at_idx", table_name="mail_messages")
    op.drop_index("mail_messages_from_idx", table_name="mail_messages")
    op.drop_index("mail_messages_thread_idx", table_name="mail_messages")
    op.drop_table("mail_messages")
