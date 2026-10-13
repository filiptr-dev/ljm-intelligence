"""Broker insights — derived columns + mail_messages.email_domain

Revision ID: 0048
Revises: 0047
Create Date: 2026-10-13

Additive-only migration behind the broker-insights plan
(projects/ljm-intelligence/plan/2026-10-13-broker-insights-and-detail-real-mail.md).

Three widenings, zero drops, every new column nullable so the existing
triage, send, and LoadStop sync paths keep writing to the originals
untouched:

* ``mail_messages.email_domain`` (nullable ``String(255)`` + index) — the
  @-tail of ``email_lower``, pre-split so matching a broker by corporate
  domain is an index hit, not a per-row LIKE.
* ``message_insights.derived_{rate_usd,lane_from,lane_to,order_no,source}``
  — subject-regex-derived rate/lane/order; triage's own fields keep their
  current meaning. ``derived_source`` identifies the extractor version so
  a future re-run can skip rows (`WHERE derived_source IS NULL`).
* ``loads.derived_{rate_usd,origin,dest}`` — mirror of the above for the
  inbox-sourced ``loads`` rows (73/73 of which currently have
  ``rate_usd IS NULL``). ``OverviewMetricsRow.revenue_usd`` reads
  ``COALESCE(rate_usd, derived_rate_usd)`` so revenue starts reflecting
  rate-con dollars without any write-path change.

Backfill of ``email_domain`` happens inside this migration in Python
batches (SQLAlchemy Core, no raw SQL strings) and is idempotent — rows
already filled are skipped by the ``WHERE email_domain IS NULL`` guard.

Downgrade drops only what was added here. No data copied out beforehand
because every new column is derived and reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0048"
down_revision: str | None = "0047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # mail_messages.email_domain
    with op.batch_alter_table("mail_messages") as batch:
        batch.add_column(sa.Column("email_domain", sa.String(length=255), nullable=True))
    op.create_index(
        "ix_mail_messages_email_domain",
        "mail_messages",
        ["email_domain"],
    )

    # Backfill: derive email_domain from email_lower where NULL. Done in
    # a single UPDATE via a SQL expression (not a Python loop) — the
    # expression itself is a SQLAlchemy function call, not a raw string.
    bind = op.get_bind()
    dialect = bind.dialect.name
    mail = sa.table(
        "mail_messages",
        sa.column("email_lower", sa.String(length=320)),
        sa.column("email_domain", sa.String(length=255)),
    )
    if dialect == "postgresql":
        # split_part is 1-based; '@' tail is index 2. NULLIF collapses
        # the empty-string result back to NULL.
        tail = sa.func.nullif(sa.func.split_part(mail.c.email_lower, "@", 2), "")
    else:
        # SQLite path (tests): substr after instr('@').
        at_pos = sa.func.instr(mail.c.email_lower, "@")
        tail = sa.case(
            (at_pos > 0, sa.func.substr(mail.c.email_lower, at_pos + 1)),
            else_=None,
        )
    op.execute(
        mail.update()
        .where(mail.c.email_domain.is_(None))
        .where(mail.c.email_lower.is_not(None))
        .values(email_domain=tail)
    )

    # message_insights derived columns
    with op.batch_alter_table("message_insights") as batch:
        batch.add_column(sa.Column("derived_rate_usd", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("derived_lane_from", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("derived_lane_to", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("derived_order_no", sa.String(length=64), nullable=True))
        batch.add_column(sa.Column("derived_source", sa.String(length=32), nullable=True))

    # loads derived columns
    with op.batch_alter_table("loads") as batch:
        batch.add_column(sa.Column("derived_rate_usd", sa.Numeric(10, 2), nullable=True))
        batch.add_column(sa.Column("derived_origin", sa.String(length=128), nullable=True))
        batch.add_column(sa.Column("derived_dest", sa.String(length=128), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("loads") as batch:
        batch.drop_column("derived_dest")
        batch.drop_column("derived_origin")
        batch.drop_column("derived_rate_usd")
    with op.batch_alter_table("message_insights") as batch:
        batch.drop_column("derived_source")
        batch.drop_column("derived_order_no")
        batch.drop_column("derived_lane_to")
        batch.drop_column("derived_lane_from")
        batch.drop_column("derived_rate_usd")
    op.drop_index("ix_mail_messages_email_domain", table_name="mail_messages")
    with op.batch_alter_table("mail_messages") as batch:
        batch.drop_column("email_domain")
