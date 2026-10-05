"""broker-rank indexes — 4 CREATE INDEX CONCURRENTLY entries, no shape change.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-05

Supports ``app.prospecting.broker_rank_service.rank_brokers`` — the SQL-side
replacement for the broker-list "load every row + compute next_action in
Python + sort" hot path (plan 2026-10-01-brokers-keyset-sql-sort). Four
indexes:

  * ``idx_leads_broker_rank`` — the composite sort key for the broker list.
    ``(tenant_id, kind, fit_score DESC NULLS LAST, lower(name), id)``.
  * ``idx_call_outcomes_lead_logged_desc`` — explicit ``(lead_id, logged_at DESC)``
    for the latest-call LATERAL. ``call_outcomes_lead_id_logged_at`` already
    exists in the same shape; kept here as a defensive no-op (``IF NOT
    EXISTS``) so a sibling migration dropping the old one wouldn't regress us.
  * ``idx_call_outcomes_pending_callback`` — partial index on
    ``(lead_id, callback_at)`` ``WHERE outcome = 'callback'`` — hits the
    earliest-pending-callback LATERAL directly.
  * ``idx_sent_log_lead_sent_desc`` — ``(lead_id, sent_at DESC)`` for the
    latest-sent LATERAL.

CONCURRENTLY so prod rolls don't lock ``leads`` during the build; the autocommit
block is required because ``CREATE INDEX CONCURRENTLY`` cannot run inside a
transaction. ``IF NOT EXISTS`` makes a re-run on a partially-upgraded DB
idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _is_postgres() -> bool:
    return op.get_bind().dialect.name == "postgresql"


def upgrade() -> None:
    if not _is_postgres():
        # Sqlite test harness: no RLS, no concurrent-index notion. Create the
        # equivalent b-tree indexes in a plain transaction so ORM-level tests
        # that rely on these shapes still see them.
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_leads_broker_rank "
            "ON leads (tenant_id, kind, fit_score, lower(name), id)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_outcomes_lead_logged_desc "
            "ON call_outcomes (lead_id, logged_at)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_call_outcomes_pending_callback "
            "ON call_outcomes (lead_id, callback_at)"
        )
        op.execute(
            "CREATE INDEX IF NOT EXISTS idx_sent_log_lead_sent_desc "
            "ON sent_log (lead_id, sent_at)"
        )
        return

    # PG16 — CREATE INDEX CONCURRENTLY requires autocommit (no outer tx).
    with op.get_context().autocommit_block():
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_leads_broker_rank "
            "ON leads (tenant_id, kind, fit_score DESC NULLS LAST, lower(name), id)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_call_outcomes_lead_logged_desc "
            "ON call_outcomes (lead_id, logged_at DESC)"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_call_outcomes_pending_callback "
            "ON call_outcomes (lead_id, callback_at) "
            "WHERE outcome = 'callback'"
        )
        op.execute(
            "CREATE INDEX CONCURRENTLY IF NOT EXISTS idx_sent_log_lead_sent_desc "
            "ON sent_log (lead_id, sent_at DESC)"
        )


def downgrade() -> None:
    if not _is_postgres():
        for name in (
            "idx_sent_log_lead_sent_desc",
            "idx_call_outcomes_pending_callback",
            "idx_call_outcomes_lead_logged_desc",
            "idx_leads_broker_rank",
        ):
            op.execute(f"DROP INDEX IF EXISTS {name}")
        return
    with op.get_context().autocommit_block():
        for name in (
            "idx_sent_log_lead_sent_desc",
            "idx_call_outcomes_pending_callback",
            "idx_call_outcomes_lead_logged_desc",
            "idx_leads_broker_rank",
        ):
            op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")
