"""procrastinate queue — Postgres-native queue + worker + scheduler schema.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-01

Installs procrastinate's own schema (tables, types, functions, triggers) via
the version-shipped ``get_schema()`` text. The procrastinate library owns the
exact object list and keeps it in lockstep with its runtime queries — pinning
the SQL into our Alembic chain means a bump-the-dep ever only reinstalls what
procrastinate itself asserts, and never spontaneously migrates in production.

Postgres-only. On SQLite (tests + dev's optional local path) the migration is
a no-op; the queue needs ``LISTEN/NOTIFY`` + ``FOR UPDATE SKIP LOCKED`` and
cannot run anywhere else. The pg16 test harness (``TEST_HARNESS=pg16``) is
how the queue is actually exercised in CI.

Round-trip: ``upgrade → downgrade -1 → upgrade`` leaves zero schema diff
(see ``tests/test_migrations_pg.py``).
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


# Keep in lock-step with the parse output in app.shared.queue DOCTRINE; this
# is the canonical "every object procrastinate 3.x installs" list, derived
# straight from the shipped schema.sql at build time.
_PROCRASTINATE_TABLES = (
    "procrastinate_events",
    "procrastinate_periodic_defers",
    "procrastinate_jobs",
    "procrastinate_workers",
)
_PROCRASTINATE_TYPES = (
    "procrastinate_job_to_defer_v1",
    "procrastinate_job_event_type",
    "procrastinate_job_status",
)
_PROCRASTINATE_FUNCTIONS = (
    "procrastinate_defer_jobs_v1",
    "procrastinate_defer_periodic_job_v2",
    "procrastinate_fetch_job_v2",
    "procrastinate_finish_job_v1",
    "procrastinate_cancel_job_v1",
    "procrastinate_retry_job_v1",
    "procrastinate_retry_job_v2",
    "procrastinate_notify_queue_job_inserted_v1",
    "procrastinate_notify_queue_abort_job_v1",
    "procrastinate_trigger_function_status_events_insert_v1",
    "procrastinate_trigger_function_status_events_update_v1",
    "procrastinate_trigger_function_scheduled_events_v1",
    "procrastinate_trigger_abort_requested_events_procedure_v1",
    "procrastinate_unlink_periodic_defers_v1",
    "procrastinate_register_worker_v1",
    "procrastinate_unregister_worker_v1",
    "procrastinate_update_heartbeat_v1",
    "procrastinate_prune_stalled_workers_v1",
)


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # Queue is Postgres-only. Keep the migration importable so the
        # SQLite dev path stays runnable through alembic upgrade head.
        return

    from procrastinate.schema import SchemaManager

    # One statement block — procrastinate's shipped schema is CREATE OR
    # REPLACE / CREATE IF NOT EXISTS throughout, so re-runs are a no-op.
    #
    # We bypass SQLAlchemy's statement execution path here: the schema SQL
    # contains literal `%` characters inside format strings in trigger
    # bodies, and psycopg's cursor.execute() parses `%` as a placeholder
    # marker. Grabbing the raw psycopg cursor and calling execute() with
    # no params makes psycopg skip placeholder substitution entirely.
    # Double literal `%` so psycopg's placeholder parser treats them as
    # literals (`%%` → `%` in psycopg's format-string resolution). The
    # procrastinate schema contains them in `format(...)` trigger bodies.
    schema_sql = SchemaManager.get_schema().replace("%", "%%")
    bind.exec_driver_sql(schema_sql)


def downgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return

    # Drop every top-level object procrastinate installs. Functions are
    # independent of the tables that call them, so we have to name them
    # explicitly — CASCADE on DROP TABLE does not drop them.
    joined_tables = ", ".join(_PROCRASTINATE_TABLES)
    bind.exec_driver_sql(f"DROP TABLE IF EXISTS {joined_tables} CASCADE")
    for fn in _PROCRASTINATE_FUNCTIONS:
        bind.exec_driver_sql(f"DROP FUNCTION IF EXISTS {fn} CASCADE")
    for enum in _PROCRASTINATE_TYPES:
        bind.exec_driver_sql(f"DROP TYPE IF EXISTS {enum} CASCADE")
