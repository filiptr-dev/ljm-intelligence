"""Migration 0043 — purge simulated demo mail without touching real rows.

The purge keys off ``mail_messages.raw ? 'demo'`` (the only marker the
simulated corpus writes, see ``simulated_corpus.build_corpus``). This test
proves two invariants:

1. A simulated row + its derived rows (message_insights, no_reply_tracker)
   are deleted.
2. A **real** row on the same mailbox (e.g. a real ``contact@ljminternational.com``
   message, which shares the staff-mailbox name with the simulated corpus)
   survives, and its downstream insight / no-reply rows survive with it.

PG16-only (uses JSONB ``?``). Skipped on sqlite.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.inbox.models import MailCursor, MailMessage, MessageInsight, NoReplyTracker
from app.shared.orm import LJM_TENANT_ID

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="PG16 harness required (TEST_HARNESS=pg16 or DATABASE_URL_TEST_PG set)",
)


# The migration body — exercised directly rather than re-running alembic
# (which has already stamped head once during conftest). Keeping the SQL in
# lockstep with the migration is intentional: if the migration changes, this
# test needs to change too, which is the signal the reviewer wants.
_PURGE_SQL = [
    """
    DELETE FROM message_insights mi
    USING mail_messages mm
    WHERE mi.tenant_id = mm.tenant_id
      AND mi.mailbox = mm.mailbox
      AND mi.message_id = mm.message_id
      AND mm.raw ? 'demo'
    """,
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
    """,
    "DELETE FROM mail_messages WHERE raw ? 'demo'",
    """
    DELETE FROM mail_cursors mc
    WHERE NOT EXISTS (
        SELECT 1 FROM mail_messages mm
        WHERE mm.tenant_id = mc.tenant_id
          AND mm.mailbox = mc.mailbox
    )
    """,
]


@pytest.mark.asyncio
async def test_purge_deletes_simulated_rows_keeps_real() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # redirected to PG16
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mbx = "contact@ljminternational.com"
    now = datetime.now(UTC)

    # Clean slate for this mailbox so we're not fighting other suite rows.
    async with maker() as s:
        await s.execute(
            text("DELETE FROM message_insights WHERE mailbox = :m"), {"m": mbx}
        )
        await s.execute(
            text("DELETE FROM no_reply_tracker WHERE mailbox = :m"), {"m": mbx}
        )
        await s.execute(text("DELETE FROM mail_messages WHERE mailbox = :m"), {"m": mbx})
        await s.execute(text("DELETE FROM mail_cursors WHERE mailbox = :m"), {"m": mbx})
        await s.commit()

    def _mm(mid: str, thread: str, demo: bool) -> MailMessage:
        return MailMessage(
            tenant_id=LJM_TENANT_ID,
            mailbox=mbx,
            message_id=mid,
            thread_id=thread,
            history_id="100",
            from_addr=f"{mid}@test",
            to_addrs=[mbx],
            cc_addrs=[],
            subject=f"s-{mid}",
            sent_at=now,
            received_at=now,
            in_reply_to=None,
            references_hdr=[],
            body_text="hi",
            body_html="",
            labels=["INBOX"],
            raw={"id": mid, "demo": {"intent": "load_offer"}} if demo else {"id": mid},
        )

    async with maker() as s:
        # Two sim + one real on the same mailbox (shared name on purpose).
        s.add_all(
            [
                _mm("sim-1", "T-sim-1", demo=True),
                _mm("sim-2", "T-sim-2", demo=True),
                _mm("real-1", "T-real-1", demo=False),
            ]
        )
        s.add_all(
            [
                MessageInsight(
                    tenant_id=LJM_TENANT_ID, mailbox=mbx, message_id="sim-1",
                    thread_id="T-sim-1", intent="load_offer",
                ),
                MessageInsight(
                    tenant_id=LJM_TENANT_ID, mailbox=mbx, message_id="real-1",
                    thread_id="T-real-1", intent="load_offer",
                ),
            ]
        )
        s.add_all(
            [
                NoReplyTracker(
                    tenant_id=LJM_TENANT_ID, mailbox=mbx,
                    thread_id="T-sim-2", we_sent_at=now,
                ),
                NoReplyTracker(
                    tenant_id=LJM_TENANT_ID, mailbox=mbx,
                    thread_id="T-real-1", we_sent_at=now,
                ),
            ]
        )
        s.add(MailCursor(tenant_id=LJM_TENANT_ID, mailbox=mbx, history_id="999"))
        await s.commit()

    # Count the marker on this mailbox before purge — our test's demo rows.
    async with maker() as s:
        marker_count = (
            await s.execute(
                text(
                    "SELECT count(*) FROM mail_messages "
                    "WHERE mailbox = :m AND raw ? 'demo'"
                ),
                {"m": mbx},
            )
        ).scalar()
    assert marker_count == 2

    # Run the migration body.
    async with maker() as s:
        for stmt in _PURGE_SQL:
            await s.execute(text(stmt))
        await s.commit()

    async with maker() as s:
        remaining_mail = (
            await s.execute(
                text("SELECT message_id FROM mail_messages WHERE mailbox = :m"),
                {"m": mbx},
            )
        ).scalars().all()
        remaining_insights = (
            await s.execute(
                text("SELECT message_id FROM message_insights WHERE mailbox = :m"),
                {"m": mbx},
            )
        ).scalars().all()
        remaining_nrt = (
            await s.execute(
                text("SELECT thread_id FROM no_reply_tracker WHERE mailbox = :m"),
                {"m": mbx},
            )
        ).scalars().all()
        cursor_rows = (
            await s.execute(
                text("SELECT history_id FROM mail_cursors WHERE mailbox = :m"),
                {"m": mbx},
            )
        ).scalars().all()

    # Real message + its insight + its no_reply row all survive.
    assert remaining_mail == ["real-1"]
    assert remaining_insights == ["real-1"]
    assert remaining_nrt == ["T-real-1"]
    # Mailbox still has a real row → cursor is preserved.
    assert cursor_rows == ["999"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_purge_drops_cursor_for_now_empty_mailbox() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # redirected to PG16
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mbx = "ops@ljminternational.com"
    now = datetime.now(UTC)

    async with maker() as s:
        await s.execute(text("DELETE FROM mail_messages WHERE mailbox = :m"), {"m": mbx})
        await s.execute(text("DELETE FROM mail_cursors WHERE mailbox = :m"), {"m": mbx})
        await s.commit()

        s.add(
            MailMessage(
                tenant_id=LJM_TENANT_ID, mailbox=mbx, message_id="sim-only",
                thread_id="T-sim-only", history_id="1001",
                from_addr="x@y", to_addrs=[mbx], cc_addrs=[],
                subject="s", sent_at=now, received_at=now, in_reply_to=None,
                references_hdr=[], body_text="", body_html="",
                labels=["INBOX"], raw={"id": "sim-only", "demo": {"intent": "praise"}},
            )
        )
        s.add(MailCursor(tenant_id=LJM_TENANT_ID, mailbox=mbx, history_id="1001"))
        await s.commit()

        for stmt in _PURGE_SQL:
            await s.execute(text(stmt))
        await s.commit()

        remaining = (
            await s.execute(
                text("SELECT count(*) FROM mail_cursors WHERE mailbox = :m"),
                {"m": mbx},
            )
        ).scalar()
    # Cursor dropped because no real messages were left for this mailbox,
    # so the next Gmail incremental starts from a clean state (historyId=0).
    assert remaining == 0

    await engine.dispose()
