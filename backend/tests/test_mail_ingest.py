"""Simulated-mailbox ingest + cursor idempotence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.mail.ingest import ingest_backfill, ingest_incremental
from app.mail.mailbox import RawMessage, SimulatedMailbox
from app.models import MailCursor, MailMessage


def _msg(mailbox: str, mid: str, hid: str, when: datetime) -> RawMessage:
    return RawMessage(
        message_id=mid,
        thread_id=f"T-{mid}",
        history_id=hid,
        mailbox=mailbox,
        from_addr=f"sender-{mid}@test",
        to_addrs=[mailbox],
        cc_addrs=[],
        subject=f"subject {mid}",
        sent_at=when,
        received_at=when,
        in_reply_to=None,
        references=[],
        body_text="hi",
        body_html="",
        labels=["INBOX"],
        raw={"id": mid},
    )


@pytest.mark.asyncio
async def test_simulated_backfill_and_cursor() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    mbx = "owner@ljm-demo.local"
    source = SimulatedMailbox(
        messages={mbx: [_msg(mbx, "M1", "100", now - timedelta(days=1)), _msg(mbx, "M2", "150", now)]}
    )

    async with maker() as s:
        stats = await ingest_backfill(s, source, mbx, months=1)
    assert stats.read == 2 and stats.upserted == 2 and stats.skipped == 0
    assert stats.last_history_id == "150"

    # Idempotence: second pass must insert nothing new.
    async with maker() as s:
        stats = await ingest_backfill(s, source, mbx, months=1)
    assert stats.upserted == 0 and stats.skipped == 2

    async with maker() as s:
        rows = (await s.execute(select(MailMessage))).scalars().all()
        cur = (await s.execute(select(MailCursor).where(MailCursor.mailbox == mbx))).scalar_one()
    assert len(rows) == 2
    assert cur.history_id == "150"


@pytest.mark.asyncio
async def test_simulated_incremental_advances_cursor() -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    mbx = "ops@ljm-demo.local"
    source = SimulatedMailbox(messages={mbx: [_msg(mbx, "N1", "200", now)]})

    # Seed cursor at 100 so N1 (history 200) qualifies.
    async with maker() as s:
        s.add(MailCursor(mailbox=mbx, history_id="100"))
        await s.commit()
        stats = await ingest_incremental(s, source, mbx)
    assert stats.upserted == 1
    assert stats.last_history_id == "200"

    async with maker() as s:
        cur = (await s.execute(select(MailCursor).where(MailCursor.mailbox == mbx))).scalar_one()
    assert cur.history_id == "200"
