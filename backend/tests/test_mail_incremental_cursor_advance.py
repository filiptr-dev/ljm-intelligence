"""Regression — ``ingest_incremental`` must advance ``mail_cursors`` across
all history pages, even a page that yields zero messages.

Context: Gmail's ``history.list`` paginates independently of the number of
``messagesAdded`` entries on a page. A page carrying only ``labelAdded`` /
``labelRemoved`` events used to leave the stored cursor behind while the
real ``historyId`` marched past Gmail's ~7-day retention window — once past,
the next incremental silently fell back to a full rebackfill and the mailbox
went quiet (contact@ + viktor@ cursors stuck at 2026-10-09 was the live
symptom).

The guard lives in the ``MailboxSource.incremental`` contract: at the end
of every page the source yields ``(None, max_history_id_seen)`` so the
caller can advance its cursor without upserting a message.

PG16-only (same harness as test_mail_purge_simulated_pg).
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.inbox.models import MailCursor, MailMessage
from app.integrations.adapters.email.ingest import ingest_incremental
from app.integrations.adapters.email.mailbox import RawMessage

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="PG16 harness required (TEST_HARNESS=pg16 or DATABASE_URL_TEST_PG set)",
)


def _msg(mailbox: str, mid: str, hid: str) -> RawMessage:
    now = datetime.now(UTC)
    return RawMessage(
        mailbox=mailbox,
        message_id=mid,
        thread_id=f"t-{mid}",
        history_id=hid,
        from_addr="alice@example.com",
        to_addrs=[mailbox],
        cc_addrs=[],
        subject=f"s-{mid}",
        sent_at=now,
        received_at=now,
        in_reply_to=None,
        references=[],
        body_text="hello",
        body_html="<p>hello</p>",
        labels=["INBOX"],
        raw={},
    )


@dataclass
class _TwoPageSource:
    """Two history pages: first has 0 ``messagesAdded`` and advances the
    cursor via the end-of-page marker; second yields exactly 1 message."""

    kind: str = "fake"
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def list_mailboxes(self) -> list[str]:  # pragma: no cover - unused
        return ["contact@regression.local"]

    async def backfill(  # pragma: no cover - unused
        self, mailbox: str, since: datetime
    ) -> AsyncIterator[RawMessage]:
        if False:
            yield  # type: ignore[unreachable]

    async def incremental(
        self, mailbox: str, start_history_id: str
    ) -> AsyncIterator[tuple[RawMessage | None, str]]:
        self.calls.append((mailbox, start_history_id))
        # Page 1 — label-only, no messagesAdded. Marker advances cursor.
        yield None, "150"
        # Page 2 — one new message at a higher historyId.
        yield _msg(mailbox, "regress-1", "180"), "180"


async def _clean(maker, mailbox: str) -> None:
    async with maker() as s:
        await s.execute(text("DELETE FROM mail_messages WHERE mailbox = :m"), {"m": mailbox})
        await s.execute(text("DELETE FROM mail_cursors WHERE mailbox = :m"), {"m": mailbox})
        await s.commit()


@pytest.mark.asyncio
async def test_cursor_advances_on_mixed_history_pages() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # redirected to PG16
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mbx = "contact@regression.local"
    await _clean(maker, mbx)
    try:
        async with maker() as s:
            stats = await ingest_incremental(s, _TwoPageSource(), mbx)
        assert stats.status == "ok"
        assert stats.upserted == 1
        assert stats.last_history_id == "180"
        async with maker() as s:
            cur = (
                await s.execute(select(MailCursor).where(MailCursor.mailbox == mbx))
            ).scalar_one()
            assert cur.history_id == "180"
            rows = (
                await s.execute(select(MailMessage).where(MailMessage.mailbox == mbx))
            ).scalars().all()
            assert len(rows) == 1
    finally:
        await _clean(maker, mbx)
        await engine.dispose()


@dataclass
class _EmptyOnlySource:
    """A single page with no messagesAdded — proves the cursor still moves."""

    kind: str = "fake"

    async def list_mailboxes(self) -> list[str]:  # pragma: no cover
        return ["viktor@regression.local"]

    async def backfill(  # pragma: no cover
        self, mailbox: str, since: datetime
    ) -> AsyncIterator[RawMessage]:
        if False:
            yield  # type: ignore[unreachable]

    async def incremental(
        self, mailbox: str, start_history_id: str
    ) -> AsyncIterator[tuple[RawMessage | None, str]]:
        # The whole page is label events — cursor must still advance.
        yield None, "9999"


@pytest.mark.asyncio
async def test_cursor_advances_when_no_messages_yielded() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")  # redirected to PG16
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    mbx = "viktor@regression.local"
    await _clean(maker, mbx)
    try:
        async with maker() as s:
            stats = await ingest_incremental(s, _EmptyOnlySource(), mbx)
        assert stats.status == "ok"
        assert stats.upserted == 0
        assert stats.last_history_id == "9999"
        async with maker() as s:
            cur = (
                await s.execute(select(MailCursor).where(MailCursor.mailbox == mbx))
            ).scalar_one()
            assert cur.history_id == "9999"
    finally:
        await _clean(maker, mbx)
        await engine.dispose()
