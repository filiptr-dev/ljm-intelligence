"""Notifications table — bell + unread badge.

Revision ID: 0049
Revises: 0048
Create Date: 2026-10-13

Additive-only. Creates a new ``notifications`` table + two supporting
indexes behind the notifications-conversations-import plan
(projects/ljm-intelligence/plan/2026-10-13-notifications-conversations-gradual-import.md).

Why a dedicated table (not a view over ``message_insights``):
``read_at`` is per-event state that must survive re-triage; keeping it
off the triage path means a notifications bug cannot corrupt an insight
row and vice versa.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0049"
down_revision: str | None = "0048"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column(
            "id",
            sa.BigInteger().with_variant(sa.Integer(), "sqlite"),
            primary_key=True,
            autoincrement=True,
        ),
        sa.Column("tenant_id", sa.String(length=64), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("thread_id", sa.String(length=255), nullable=True),
        sa.Column("message_id", sa.String(length=255), nullable=True),
        sa.Column("load_id", sa.BigInteger(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_notifications_tenant_unread",
        "notifications",
        ["tenant_id", "read_at", "created_at"],
    )
    op.create_index(
        "ix_notifications_tenant_created",
        "notifications",
        ["tenant_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_tenant_created", table_name="notifications")
    op.drop_index("ix_notifications_tenant_unread", table_name="notifications")
    op.drop_table("notifications")
