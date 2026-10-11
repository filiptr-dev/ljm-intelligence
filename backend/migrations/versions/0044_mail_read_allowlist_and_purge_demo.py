"""Mail-read allowlist + purge of demo seed data

Revision ID: 0044
Revises: 0043
Create Date: 2026-10-11

Two concerns bundled so a fresh ``alembic upgrade head`` lands in the same
clean state as the live-reset path. If they split, a dev running
``docker compose down -v`` and coming back up would re-seed the demo
corpus the live reset just purged.

Schema change
-------------
Add ``settings.mail_read_allowlist`` as JSONB NULL (JSON on sqlite) — the
mail ingest side filters its ``list_mailboxes`` fan-out through this list
and refuses to touch any mailbox not in it. ``NULL`` keeps today's
behaviour (every mailbox is read). The LJM singleton row (id=1) is seeded
with ``[contact@ljminternational.com, viktor@ljminternational.com]``.

Purge (Postgres only)
---------------------
Mirrors the dialect guard pattern from 0043 — the ``?`` JSONB existence
operator does not exist on sqlite, and the test harness runs against pg16.
Rows deleted:

* ``freight_runs`` where ``source='demo'`` — seeded by 0036/0037/0039.
* Children of demo trucks (``truck_inspections``, ``truck_defects``,
  ``truck_maintenance``, ``truck_documents``), then null out
  ``freight_runs.truck_id`` for runs still pointing at demo trucks (the
  FK is ``ON DELETE SET NULL`` but we belt-and-brace the order), then
  ``trucks`` where ``source='demo'``.
* ``loads`` where ``source IN ('demo','demo2','inbox')`` — fixtures from
  the ``/loads/demo`` seed and anything the simulated inbox ever
  extracted.
* ``broker_predictions`` + ``prediction_runs`` where kind is
  ``analysis_nightly`` — safe to drop; ``analysis.nightly`` is
  DELETE-then-INSERT-per-snapshot so it regenerates from real data.
* ``lane_insights_cache`` — regenerates against real data (same rationale
  as 0039 dropping it).
* 0043 scope re-applied — ``message_insights`` / ``no_reply_tracker`` /
  ``mail_messages`` where ``raw ? 'demo'``, and empty ``mail_cursors``.
  Idempotent for a DB that already ran 0043; catches a local DB that
  skipped it.

Deliberately NOT purged (decided from code, not asked):
``organizations``, ``users``, ``settings`` itself (keep brand, caps, JWT
secret, DB-stored SA creds), ``operating_area`` (real business rule —
"east of Texas"), ``email_templates``, ``tenant_credentials``,
``tenant_feature_flags``.

Downgrade: schema column kept (data columns can be safely dropped but the
purge is one-way). No-op mirrors 0043.
"""

from __future__ import annotations

import json
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0044"
down_revision: str | None = "0043"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DEFAULT_ALLOWLIST = [
    "contact@ljminternational.com",
    "viktor@ljminternational.com",
]


def _jsonb():
    return sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name

    # 1. Schema — allowlist column + seed.
    op.add_column(
        "settings",
        sa.Column("mail_read_allowlist", _jsonb(), nullable=True),
    )
    # Seed the singleton row. Insert if missing; otherwise update.
    if dialect == "postgresql":
        bind.execute(
            sa.text(
                "UPDATE settings SET mail_read_allowlist = CAST(:v AS JSONB) "
                "WHERE id = 1"
            ),
            {"v": json.dumps(_DEFAULT_ALLOWLIST)},
        )
    else:
        bind.execute(
            sa.text(
                "UPDATE settings SET mail_read_allowlist = :v WHERE id = 1"
            ),
            {"v": json.dumps(_DEFAULT_ALLOWLIST)},
        )

    # 2. Purge — Postgres only.
    if dialect != "postgresql":
        return

    # freight_runs demo rows (seeded by 0036/0037/0039). Null out truck_id
    # references first so the subsequent trucks delete does not trip FK
    # ordering even without ON DELETE SET NULL.
    op.execute(
        """
        UPDATE freight_runs SET truck_id = NULL
        WHERE truck_id IN (SELECT id FROM trucks WHERE source = 'demo')
        """
    )
    op.execute("DELETE FROM freight_runs WHERE source = 'demo'")

    # Trucks + their children (seeded by 0038).
    for child in (
        "truck_inspections",
        "truck_defects",
        "truck_maintenance",
        "truck_documents",
    ):
        op.execute(
            f"""
            DELETE FROM {child}
            WHERE truck_id IN (SELECT id FROM trucks WHERE source = 'demo')
            """
        )
    op.execute("DELETE FROM trucks WHERE source = 'demo'")

    # Loads: demo + demo2 fixtures, plus anything derived from the simulated
    # inbox corpus.
    op.execute("DELETE FROM loads WHERE source IN ('demo', 'demo2', 'inbox')")

    # Analysis snapshots — regenerate from real data on the next run.
    # ``analysis.nightly`` is DELETE-then-INSERT-per-snapshot, so wiping
    # these is a free no-op the next time it runs.
    op.execute("DELETE FROM broker_predictions")
    op.execute("DELETE FROM prediction_runs WHERE kind = 'analysis_nightly'")

    # Lane insights — regenerate.
    op.execute("DELETE FROM lane_insights_cache")

    # Re-apply 0043's mail purge for a local DB that skipped it or added
    # new demo rows via a later ingest.
    op.execute(
        """
        DELETE FROM message_insights mi
        USING mail_messages mm
        WHERE mi.tenant_id = mm.tenant_id
          AND mi.mailbox = mm.mailbox
          AND mi.message_id = mm.message_id
          AND mm.raw ? 'demo'
        """
    )
    op.execute(
        """
        DELETE FROM no_reply_tracker nrt
        WHERE EXISTS (
            SELECT 1 FROM mail_messages mm
            WHERE mm.tenant_id = nrt.tenant_id
              AND mm.mailbox = nrt.mailbox
              AND mm.thread_id = nrt.thread_id
              AND mm.raw ? 'demo'
        )
        AND NOT EXISTS (
            SELECT 1 FROM mail_messages mm2
            WHERE mm2.tenant_id = nrt.tenant_id
              AND mm2.mailbox = nrt.mailbox
              AND mm2.thread_id = nrt.thread_id
              AND NOT (mm2.raw ? 'demo')
        )
        """
    )
    op.execute("DELETE FROM mail_messages WHERE raw ? 'demo'")
    op.execute(
        """
        DELETE FROM mail_cursors mc
        WHERE NOT EXISTS (
            SELECT 1 FROM mail_messages mm
            WHERE mm.tenant_id = mc.tenant_id
              AND mm.mailbox = mc.mailbox
        )
        """
    )


def downgrade() -> None:
    # Keep the column (safely nullable); the purge is one-way by design.
    pass
