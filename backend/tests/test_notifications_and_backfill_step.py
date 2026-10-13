"""Targeted tests for the notifications bell + gradual-import plan.

Covers only the new seams in the plan's ACs:
  * ``_update_cursor`` is monotone on ``history_id``.
  * ``create_from_message`` gates on 24h recency (backfill never notifies).
  * A forced exception inside notifications does NOT fail ``_upsert``.
  * ``list_counterpart_threads`` returns the right rows.
  * ``mail_backfill_step`` steps ``backfilled_through_at`` backwards.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # register all tables with Base.metadata
from app.db import Base
from app.inbox import notifications_service as nsvc, service as inbox_svc
from app.inbox.models import MailCursor, MailMessage, Notification

_ = app.models  # keep import for side-effect
from app.integrations.adapters.email.ingest import (
    _update_cursor,
    ingest_backfill,
)
from app.integrations.adapters.email.mailbox import RawMessage, SimulatedMailbox


def _msg(
    *,
    mid: str,
    thread: str,
    mailbox: str = "owner@ljm.com",
    from_addr: str = "scott@chrobinson.com",
    to_addr: str = "owner@ljm.com",
    subject: str = "Hi",
    body: str = "Hello",
    sent_at: datetime | None = None,
    in_reply_to: str | None = None,
    history_id: str = "1000",
) -> RawMessage:
    sent_at = sent_at or datetime.now(UTC) - timedelta(hours=1)
    return RawMessage(
        message_id=mid,
        thread_id=thread,
        history_id=history_id,
        mailbox=mailbox,
        from_addr=from_addr,
        to_addrs=[to_addr],
        cc_addrs=[],
        subject=subject,
        sent_at=sent_at,
        received_at=sent_at,
        in_reply_to=in_reply_to,
        references=[],
        body_text=body,
        body_html=f"<p>{body}</p>",
        labels=["INBOX"],
        raw={"id": mid, "snippet": body[:80]},
    )


async def _fresh_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return maker()


# ---- Task 3: monotone cursor + backfill guard ------------------------------


@pytest.mark.asyncio
async def test_update_cursor_is_monotone() -> None:
    async with await _fresh_session() as s:
        await _update_cursor(s, "mbx@a.com", "5000", datetime.now(UTC))
        await s.commit()
        # Try to move backwards — must stick at 5000.
        await _update_cursor(s, "mbx@a.com", "3000", None)
        await s.commit()
        cur = (await s.execute(select(MailCursor))).scalar_one()
        assert cur.history_id == "5000"
        # Forward move still works.
        await _update_cursor(s, "mbx@a.com", "6000", None)
        await s.commit()
        cur = (await s.execute(select(MailCursor))).scalar_one()
        assert cur.history_id == "6000"


# ---- Task 1: notifications bell -------------------------------------------


@pytest.mark.asyncio
async def test_backfill_never_notifies_old_mail() -> None:
    async with await _fresh_session() as s:
        old = datetime.now(UTC) - timedelta(days=10)
        msg = _msg(mid="old-1", thread="T-old", sent_at=old)
        src = SimulatedMailbox(messages={msg.mailbox: [msg]})
        stats = await ingest_backfill(s, src, msg.mailbox, months=12)
        assert stats.status == "ok"
        assert stats.upserted == 1
        rows = (await s.execute(select(Notification))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_broker_reply_notification_within_24h() -> None:
    async with await _fresh_session() as s:
        # Seed: an outbound message we sent on the thread.
        out = _msg(
            mid="m-out", thread="T-rep", mailbox="owner@ljm.com",
            from_addr="owner@ljm.com", to_addr="scott@chrobinson.com",
            subject="Hi Scott", body="Any loads?",
            sent_at=datetime.now(UTC) - timedelta(hours=3),
            demo_passthrough=False,  # type: ignore[call-arg]
        ) if False else _msg(
            mid="m-out", thread="T-rep", mailbox="owner@ljm.com",
            from_addr="owner@ljm.com", to_addr="scott@chrobinson.com",
            subject="Hi Scott", body="Any loads?",
            sent_at=datetime.now(UTC) - timedelta(hours=3),
        )
        src1 = SimulatedMailbox(messages={out.mailbox: [out]})
        await ingest_backfill(s, src1, out.mailbox, months=12)
        # Inbound reply from broker on same thread, fresh.
        reply = _msg(
            mid="m-in", thread="T-rep", mailbox="owner@ljm.com",
            from_addr="scott@chrobinson.com", to_addr="owner@ljm.com",
            subject="Re: Hi Scott", body="Got a van LA->DAL",
            sent_at=datetime.now(UTC) - timedelta(minutes=5),
            in_reply_to="<m-out@test>",
            history_id="2000",
        )
        src2 = SimulatedMailbox(messages={reply.mailbox: [reply]})
        await ingest_backfill(s, src2, reply.mailbox, months=12)
        rows = (await s.execute(select(Notification))).scalars().all()
        kinds = sorted({r.kind for r in rows})
        assert "broker_reply" in kinds


@pytest.mark.asyncio
async def test_notification_failure_does_not_break_upsert(monkeypatch) -> None:
    async def _boom(*_a, **_k):
        raise RuntimeError("induced")

    monkeypatch.setattr(nsvc, "create_from_message", _boom)
    async with await _fresh_session() as s:
        msg = _msg(
            mid="m-safe", thread="T-safe",
            sent_at=datetime.now(UTC) - timedelta(minutes=10),
        )
        src = SimulatedMailbox(messages={msg.mailbox: [msg]})
        stats = await ingest_backfill(s, src, msg.mailbox, months=12)
        assert stats.status == "ok"
        assert stats.upserted == 1
        # MailMessage must still have been written.
        mm = (await s.execute(select(MailMessage))).scalars().all()
        assert len(mm) == 1


@pytest.mark.asyncio
async def test_mark_read_and_all() -> None:
    async with await _fresh_session() as s:
        s.add(Notification(kind="urgent_email", title="x"))
        s.add(Notification(kind="broker_reply", title="y"))
        await s.commit()
        assert await nsvc.count_unread(s) == 2
        changed = await nsvc.mark_all_read(s)
        assert changed == 2
        await s.commit()
        assert await nsvc.count_unread(s) == 0


# ---- Task 2: counterpart threads ------------------------------------------


@pytest.mark.asyncio
async def test_list_counterpart_threads_matches_by_domain() -> None:
    async with await _fresh_session() as s:
        mbx = "owner@ljm.com"
        # Thread A — broker scott@chrobinson.com
        a = _msg(mid="a1", thread="T-A", from_addr="scott@chrobinson.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=1))
        # Thread B — different sender, same corp domain.
        b = _msg(mid="b1", thread="T-B", from_addr="dave@chrobinson.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=2))
        # Thread C — unrelated domain.
        c = _msg(mid="c1", thread="T-C", from_addr="x@tql.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=3))
        src = SimulatedMailbox(messages={mbx: [a, b, c]})
        await ingest_backfill(s, src, mbx, months=12)
        # Fill email_domain the way migration 0048 does — not run against sqlite
        # autogen here, so stamp it manually to mirror prod.
        for row in (await s.execute(select(MailMessage))).scalars().all():
            if row.email_lower and "@" in row.email_lower:
                row.email_domain = row.email_lower.split("@", 1)[1]
        await s.commit()

        result = await inbox_svc.list_counterpart_threads(s, "T-A")
        assert result is not None
        thread_ids = {r.thread_id for r in result.items}
        assert "T-B" in thread_ids
        assert "T-C" not in thread_ids
        assert "T-A" not in thread_ids


@pytest.mark.asyncio
async def test_list_counterpart_threads_free_mail_only_matches_email() -> None:
    async with await _fresh_session() as s:
        mbx = "owner@ljm.com"
        a = _msg(mid="a1", thread="T-A", from_addr="joe@gmail.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=1))
        b = _msg(mid="b1", thread="T-B", from_addr="joe@gmail.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=2))
        c = _msg(mid="c1", thread="T-C", from_addr="other@gmail.com",
                 sent_at=datetime.now(UTC) - timedelta(hours=3))
        src = SimulatedMailbox(messages={mbx: [a, b, c]})
        await ingest_backfill(s, src, mbx, months=12)
        for row in (await s.execute(select(MailMessage))).scalars().all():
            if row.email_lower and "@" in row.email_lower:
                row.email_domain = row.email_lower.split("@", 1)[1]
        await s.commit()

        result = await inbox_svc.list_counterpart_threads(s, "T-A")
        assert result is not None
        tids = {r.thread_id for r in result.items}
        assert "T-B" in tids       # same email
        assert "T-C" not in tids   # free-mail domain can't corroborate


# ---- Task 3: mail_backfill_step marches backfilled_through_at ---------------


@pytest.mark.asyncio
async def test_ingest_backfill_window_updates_through_at() -> None:
    async with await _fresh_session() as s:
        mbx = "owner@ljm.com"
        now = datetime.now(UTC)
        older_than = now - timedelta(days=45)
        newer_than = now - timedelta(days=24)
        # Seed one message inside the window.
        m = _msg(mid="w1", thread="T-W", from_addr="scott@chrobinson.com",
                 sent_at=now - timedelta(days=30), history_id="7777")
        src = SimulatedMailbox(messages={mbx: [m]})
        stats = await ingest_backfill(
            s, src, mbx, window=(older_than, newer_than),
        )
        assert stats.status == "ok"
        cur = (await s.execute(select(MailCursor))).scalar_one()
        assert cur.backfilled_through_at is not None
        # The through_at marches backward to ``older_than``. SQLite returns
        # naive datetimes — compare in UTC.
        got = cur.backfilled_through_at
        if got.tzinfo is None:
            got = got.replace(tzinfo=UTC)
        assert abs((got - older_than).total_seconds()) < 5
