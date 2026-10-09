"""Purge simulated demo mail corpus from mail_messages + derived rows.

Revision ID: 0043
Revises: 0042
Create Date: 2026-10-09

Context: prod ``mail_messages`` holds ~150 simulated demo emails (seeded by
``SimulatedMailbox`` + ``simulated_corpus.build_corpus``). Real + simulated
rows live in the same table with no ``source`` column. The one reliable
distinguisher is the per-row marker the corpus builder writes:
``mail_messages.raw ? 'demo'`` — every simulated row carries a non-null
``raw['demo']`` dict (``intent``/``sentiment``/``is_inbound``/...); no real
Gmail message carries that key. The mailbox name is **not** a marker — the
simulated staff mailboxes (``contact@``, ``ops@``, ``dispatch@``,
``accounts@``) share names with the real @ljminternational.com mailboxes.

Deletes, in order, scoped to rows whose root ``mail_messages`` row carries
the marker:
  * ``message_insights`` by (tenant_id, mailbox, message_id)
  * ``no_reply_tracker`` by (tenant_id, mailbox, thread_id) *only when the
    thread has no non-demo message left on it* (so a thread with any real
    reply survives)
  * ``mail_messages`` by the marker itself
  * ``mail_cursors`` for mailboxes that are now empty (no real rows remain) —
    their history_id points at a simulated history number; letting it stand
    would wedge the next incremental run

Downgrade is a no-op — a demo corpus can be re-seeded at any time by
re-running ingest against ``SimulatedMailbox``.

Postgres only: the ``?`` JSONB existence operator is used — the test harness
runs on pg16 (TEST_HARNESS=pg16) and prod is Neon. SQLite ignores migration
0016 side-stepping already; this follows the same convention.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0043"
down_revision: str | None = "0042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    # Only run against Postgres — the ``?`` JSONB existence operator does not
    # exist on sqlite. On sqlite there is nothing to purge (demo data lives
    # in in-memory tests).
    if bind.dialect.name != "postgresql":
        return

    # 1. message_insights for simulated messages.
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

    # 2. no_reply_tracker rows whose thread has NO non-demo message left.
    #    A thread with any real message on it must survive — the tracker row
    #    is still meaningful.
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

    # 3. The simulated messages themselves.
    op.execute("DELETE FROM mail_messages WHERE raw ? 'demo'")

    # 4. Cursors for mailboxes now empty — their stored history_id is a
    #    simulated counter (1000+N) that would wedge the next Gmail
    #    incremental call. Leave cursors alone for mailboxes with real rows.
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
    # No-op by design — demo corpus can always be re-seeded by running
    # ingest against SimulatedMailbox again.
    pass
