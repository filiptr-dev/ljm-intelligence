"""fetch_needs_reply — latest-inbound-per-thread filter + exclusions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.inbox import service as svc
from app.inbox.models import MailMessage, MessageInsight


@pytest.fixture()
async def session():
    """One-off aiosqlite engine, fresh schema per test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = sessionmaker(engine, class_=__import__("sqlalchemy.ext.asyncio", fromlist=["AsyncSession"]).AsyncSession, expire_on_commit=False)
    async with Session() as s:
        yield s
    await engine.dispose()


def _mm(**kw):
    base = {
        "tenant_id": "01LJMORGLJM00000000000000A",
        "history_id": "1", "labels": ["INBOX"], "to_addrs": [],
        "received_at": datetime.now(UTC), "ingested_at": datetime.now(UTC),
    }
    base.update(kw)
    return MailMessage(**base)


def _ins(**kw):
    base = {
        "tenant_id": "01LJMORGLJM00000000000000A",
        "intent": "routine", "urgency": "normal",
        "sentiment": 0.0, "confidence": 0.5, "model": "demo", "model_version": "demo-v1",
    }
    base.update(kw)
    return MessageInsight(**base)


@pytest.mark.asyncio
async def test_needs_reply_picks_latest_inbound():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add_all([
            # Thread T1: inbound latest — SHOULD appear.
            _mm(mailbox="contact@ljminternational.com", message_id="m1", thread_id="T1",
                from_addr="bob@acme.com", subject="Load from Chicago",
                sent_at=now - timedelta(hours=2)),
            _ins(mailbox="contact@ljminternational.com", message_id="m1", thread_id="T1",
                 intent="load_offer", urgency="normal", broker_name="Bob"),
            # Thread T2: our outbound latest — should NOT appear.
            _mm(mailbox="contact@ljminternational.com", message_id="m2", thread_id="T2",
                from_addr="contact@ljminternational.com", subject="Re: Load",
                sent_at=now - timedelta(hours=1)),
            # Thread T3: inbound but auto-reply subject — excluded.
            _mm(mailbox="contact@ljminternational.com", message_id="m3", thread_id="T3",
                from_addr="autobot@foo.com", subject="Out of office — back Monday",
                sent_at=now),
            # Thread T4: inbound routine-low — excluded.
            _mm(mailbox="contact@ljminternational.com", message_id="m4", thread_id="T4",
                from_addr="jane@xyz.com", subject="FYI tracking",
                sent_at=now - timedelta(minutes=30)),
            _ins(mailbox="contact@ljminternational.com", message_id="m4", thread_id="T4",
                 intent="routine", urgency="low"),
            # Thread T5: inbound from our own domain — excluded.
            _mm(mailbox="contact@ljminternational.com", message_id="m5", thread_id="T5",
                from_addr="viktor@ljminternational.com", subject="FW: internal",
                sent_at=now - timedelta(hours=3)),
        ])
        await s.commit()

        rows = await svc.fetch_needs_reply(s, mailbox=None, limit=10)
        tids = [r.thread_id for r in rows]
        assert "T1" in tids
        assert "T2" not in tids
        assert "T3" not in tids
        assert "T4" not in tids
        assert "T5" not in tids
        # Row carries intent + urgency from the matched insight.
        t1 = next(r for r in rows if r.thread_id == "T1")
        assert t1.intent == "load_offer"
        assert t1.broker_name == "Bob"
    await engine.dispose()


@pytest.mark.asyncio
async def test_needs_reply_mailbox_filter():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    async with sm() as s:
        s.add_all([
            _mm(mailbox="contact@ljminternational.com", message_id="a", thread_id="A",
                from_addr="x@c.com", subject="hello", sent_at=now),
            _mm(mailbox="viktor@ljminternational.com", message_id="b", thread_id="B",
                from_addr="y@c.com", subject="hello", sent_at=now),
        ])
        await s.commit()
        only = await svc.fetch_needs_reply(s, mailbox="contact@ljminternational.com", limit=10)
        assert [r.thread_id for r in only] == ["A"]
    await engine.dispose()
