"""inbox analysis — message_insights + no_reply_tracker + runtime stamping cols

Revision ID: 0018
Revises: 0017
Create Date: 2026-10-01

What this migration adds (scoped to this coder pass, per the plan):

  * ``message_insights`` — one row per analysed message (intent + urgency +
    sentiment + confidence + model_version). Unique on
    ``(tenant_id, mailbox, message_id, model_version)`` so re-triaging with
    a new model version is a new row, not an update.
  * ``no_reply_tracker`` — one row per outbound-last thread we're still
    waiting on. Keyed by ``(tenant_id, mailbox, thread_id)``; dropped when
    an inbound reply lands.

It also fills two foreseen-but-not-yet-populated columns on
``mail_messages``:

  * ``email_lower`` — now stamped by ingest for every new row. The 0016
    migration added the column + the ``(tenant_id, email_lower)`` index
    but only backfilled existing rows. This migration adds the plain
    ``ix_mail_messages_email_lower`` index the analysis queries use.
  * ``retention_until`` — ingest stamps ``now() + interval '18 months'``.

Postgres-safe. Round-tripped on PG16 via the tests/test_migrations_pg.py
upgrade→downgrade→upgrade loop.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ---- message_insights ----
    op.create_table(
        "message_insights",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(26), nullable=False,
                  server_default="01LJMORGLJM00000000000000A"),
        sa.Column("mailbox", sa.String(255), nullable=False),
        sa.Column("message_id", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("from_email_normalized", sa.String(320), nullable=False,
                  server_default=""),
        sa.Column("intent", sa.String(32), nullable=False),
        sa.Column("urgency", sa.String(16), nullable=False, server_default="normal"),
        sa.Column("sentiment", sa.Float(), nullable=False, server_default="0"),
        sa.Column("confidence", sa.Float(), nullable=False, server_default="0"),
        sa.Column("rate_usd", sa.Numeric(10, 2), nullable=True),
        sa.Column("lane_from", sa.String(128), nullable=True),
        sa.Column("lane_to", sa.String(128), nullable=True),
        sa.Column("equipment", sa.String(64), nullable=True),
        sa.Column("broker_name", sa.String(255), nullable=True),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("model", sa.String(64), nullable=False, server_default="demo"),
        sa.Column("model_version", sa.String(32), nullable=False, server_default="demo-v1"),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_message_insights_tenant", "message_insights", ["tenant_id"])
    op.create_index("ix_message_insights_thread", "message_insights",
                    ["tenant_id", "thread_id"])
    op.create_index("ix_message_insights_intent", "message_insights",
                    ["tenant_id", "intent"])
    op.create_index("ix_message_insights_from", "message_insights",
                    ["tenant_id", "from_email_normalized"])
    op.create_unique_constraint(
        "uq_message_insights_msg_model",
        "message_insights",
        ["tenant_id", "mailbox", "message_id", "model_version"],
    )

    # ---- no_reply_tracker ----
    op.create_table(
        "no_reply_tracker",
        sa.Column("id", sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
                  primary_key=True, autoincrement=True),
        sa.Column("tenant_id", sa.String(26), nullable=False,
                  server_default="01LJMORGLJM00000000000000A"),
        sa.Column("mailbox", sa.String(255), nullable=False),
        sa.Column("thread_id", sa.String(255), nullable=False),
        sa.Column("to_email_normalized", sa.String(320), nullable=False,
                  server_default=""),
        sa.Column("subject", sa.Text(), nullable=False, server_default=""),
        sa.Column("we_sent_at", sa.TIMESTAMP(timezone=True), nullable=False),
        sa.Column("follow_up_suggested_at", sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column("created_at", sa.TIMESTAMP(timezone=True),
                  nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_no_reply_tenant", "no_reply_tracker", ["tenant_id"])
    op.create_unique_constraint(
        "uq_no_reply_thread",
        "no_reply_tracker",
        ["tenant_id", "mailbox", "thread_id"],
    )

    # ---- helper index on mail_messages.email_lower (0016 added column + composite index) ----
    op.create_index(
        "ix_mail_messages_email_lower",
        "mail_messages",
        ["email_lower"],
    )

    # ---- RLS policies (Postgres only) ----
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        for table in ("message_insights", "no_reply_tracker"):
            bind.exec_driver_sql(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
            bind.exec_driver_sql(
                f"""
                CREATE POLICY {table}_tenant_isolation ON {table}
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
        for table in ("message_insights", "no_reply_tracker"):
            bind.exec_driver_sql(f"DROP POLICY IF EXISTS {table}_tenant_isolation ON {table}")
            bind.exec_driver_sql(f"ALTER TABLE {table} DISABLE ROW LEVEL SECURITY")

    op.drop_index("ix_mail_messages_email_lower", table_name="mail_messages")
    op.drop_constraint("uq_no_reply_thread", "no_reply_tracker", type_="unique")
    op.drop_index("ix_no_reply_tenant", table_name="no_reply_tracker")
    op.drop_table("no_reply_tracker")

    op.drop_constraint("uq_message_insights_msg_model", "message_insights", type_="unique")
    op.drop_index("ix_message_insights_from", table_name="message_insights")
    op.drop_index("ix_message_insights_intent", table_name="message_insights")
    op.drop_index("ix_message_insights_thread", table_name="message_insights")
    op.drop_index("ix_message_insights_tenant", table_name="message_insights")
    op.drop_table("message_insights")
