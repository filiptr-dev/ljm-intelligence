"""Inbox analysis — triage-on-ingest, service queries.

Scope: the mechanics the plan calls for in this coder pass.
  * triage_message() uses raw["demo"] labels verbatim → fast + no AI call.
  * ingest writes a message_insights row per persisted message.
  * list_emails / sentiment_buckets / triage_list / overview_kpis return
    the expected shapes.
  * load_offer → loads row with source='inbox'.
  * no_reply_tracker: outbound creates it, inbound clears it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.inbox import service as inbox_svc
from app.inbox.models import MessageInsight, NoReplyTracker
from app.inbox.triage import triage_message
from app.integrations.adapters.email.ingest import ingest_backfill
from app.integrations.adapters.email.mailbox import RawMessage, SimulatedMailbox
from app.prospecting.models import Load


def _msg(
    *,
    mid: str = "m1",
    thread: str = "T-abc",
    mailbox: str = "contact@ljminternational.com",
    from_addr: str = "scott@chrobinson.com",
    to_addr: str = "contact@ljminternational.com",
    subject: str = "Load offer: Los Angeles, CA to Dallas, TX",
    body: str = "Hi team, we have a Van load. Target rate $2200.",
    sent_at: datetime | None = None,
    demo: dict | None = None,
) -> RawMessage:
    sent_at = sent_at or datetime.now(UTC) - timedelta(hours=2)
    raw: dict = {"id": mid, "snippet": body[:80]}
    if demo is not None:
        raw["demo"] = demo
    return RawMessage(
        message_id=mid,
        thread_id=thread,
        history_id="1000",
        mailbox=mailbox,
        from_addr=from_addr,
        to_addrs=[to_addr],
        cc_addrs=[],
        subject=subject,
        sent_at=sent_at,
        received_at=sent_at,
        in_reply_to=None,
        references=[],
        body_text=body,
        body_html=f"<p>{body}</p>",
        labels=["INBOX"],
        raw=raw,
    )


async def _fresh_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return maker()


async def _ingest(session: AsyncSession, msgs: list[RawMessage]) -> None:
    """Full ingest path — writes both MailMessage AND MessageInsight (via triage)."""
    by_mbx: dict[str, list[RawMessage]] = {}
    for m in msgs:
        by_mbx.setdefault(m.mailbox, []).append(m)
    for mbx, mlist in by_mbx.items():
        src = SimulatedMailbox(messages={mbx: mlist})
        await ingest_backfill(session, src, mbx, months=6)


@pytest.mark.asyncio
async def test_triage_uses_demo_labels_verbatim() -> None:
    async with await _fresh_session() as s:
        msg = _msg(demo={"intent": "complaint", "sentiment": -0.6, "is_inbound": True, "broker": "CH Robinson"})
        out = await triage_message(s, msg)
        await s.commit()
        assert out.intent == "complaint"
        assert out.urgency == "urgent"
        assert out.sentiment == -0.6
        insight = (await s.execute(select(MessageInsight))).scalar_one()
        assert insight.intent == "complaint"
        assert insight.model == "demo"
        assert insight.model_version == "demo-v1"


@pytest.mark.asyncio
async def test_triage_fallback_rules_without_demo() -> None:
    async with await _fresh_session() as s:
        msg = _msg(
            subject="URGENT — truck needed Chicago today",
            body="Need a truck in Chicago TODAY. This is live and the shipper is pushing hard.",
            demo=None,
        )
        out = await triage_message(s, msg)
        await s.commit()
        assert out.intent == "urgent_truck"
        assert out.urgency == "urgent"


@pytest.mark.asyncio
async def test_triage_is_idempotent() -> None:
    async with await _fresh_session() as s:
        msg = _msg(demo={"intent": "praise", "sentiment": 0.7, "is_inbound": True, "broker": "TQL"})
        await triage_message(s, msg)
        await triage_message(s, msg)
        await s.commit()
        rows = (await s.execute(select(MessageInsight))).scalars().all()
        assert len(rows) == 1


@pytest.mark.asyncio
async def test_load_offer_creates_loads_row() -> None:
    async with await _fresh_session() as s:
        msg = _msg(
            demo={
                "intent": "load_offer",
                "sentiment": 0.1,
                "is_inbound": True,
                "broker": "CH Robinson",
                "lane_from": "Los Angeles, CA",
                "lane_to": "Dallas, TX",
                "rate": 2200,
            }
        )
        await triage_message(s, msg)
        await s.commit()
        load = (await s.execute(select(Load).where(Load.source == "inbox"))).scalar_one()
        assert load.broker_name == "CH Robinson"
        assert load.origin_city == "Los Angeles"
        assert load.dest_city == "Dallas"
        assert float(load.rate_usd) == 2200.0


@pytest.mark.asyncio
async def test_no_reply_tracker_outbound_in_inbound_out() -> None:
    async with await _fresh_session() as s:
        out = _msg(
            mid="m-out", thread="T-nr", from_addr="contact@ljminternational.com",
            to_addr="scott@chrobinson.com",
            demo={"intent": "rate_request", "sentiment": 0.0, "is_inbound": False, "broker": "CH Robinson"},
        )
        await triage_message(s, out)
        await s.commit()
        rows = (await s.execute(select(NoReplyTracker))).scalars().all()
        assert len(rows) == 1
        assert rows[0].thread_id == "T-nr"

        reply = _msg(
            mid="m-reply", thread="T-nr", from_addr="scott@chrobinson.com",
            to_addr="contact@ljminternational.com",
            demo={"intent": "booked", "sentiment": 0.5, "is_inbound": True, "broker": "CH Robinson"},
        )
        await triage_message(s, reply)
        await s.commit()
        rows = (await s.execute(select(NoReplyTracker))).scalars().all()
        assert rows == []


@pytest.mark.asyncio
async def test_sentiment_buckets_inbound_only() -> None:
    async with await _fresh_session() as s:
        msgs = [
            _msg(mid="i1", from_addr="scott@chrobinson.com", to_addr="contact@ljminternational.com",
                 demo={"intent": "praise", "sentiment": 0.6, "is_inbound": True, "broker": "x"}),
            _msg(mid="i2", from_addr="mark@tql.com", to_addr="contact@ljminternational.com",
                 demo={"intent": "complaint", "sentiment": -0.5, "is_inbound": True, "broker": "x"}),
            _msg(mid="o1", from_addr="contact@ljminternational.com", to_addr="scott@chrobinson.com",
                 demo={"intent": "routine", "sentiment": 0.4, "is_inbound": False, "broker": "x"}),
        ]
        await _ingest(s, msgs)
        b = await inbox_svc.sentiment_buckets(s)
        assert b.inbound_total == 2
        assert b.positive == 1
        assert b.negative == 1
        assert b.neutral == 0


@pytest.mark.asyncio
async def test_list_emails_shape() -> None:
    async with await _fresh_session() as s:
        await _ingest(
            s,
            [
                _msg(demo={"intent": "load_offer", "sentiment": 0.1, "is_inbound": True, "broker": "CH Robinson",
                           "lane_from": "LA, CA", "lane_to": "Dallas, TX", "rate": 2000}),
            ],
        )
        rows, total = await inbox_svc.list_emails(s, limit=10)
        assert total == 1
        assert rows[0].intent == "load_offer"
        assert rows[0].direction == "in"


@pytest.mark.asyncio
async def test_triage_list_puts_urgent_first() -> None:
    now = datetime.now(UTC)
    async with await _fresh_session() as s:
        await _ingest(
            s,
            [
                _msg(mid="r1", thread="T-r", sent_at=now - timedelta(minutes=5),
                     demo={"intent": "routine", "sentiment": 0.0, "is_inbound": True, "broker": "x"}),
                _msg(mid="u1", thread="T-u", sent_at=now - timedelta(hours=3),
                     demo={"intent": "urgent_truck", "sentiment": -0.3, "is_inbound": True, "broker": "x"}),
            ],
        )
        items = await inbox_svc.triage_list(s, limit=10)
        assert items[0].urgency == "urgent"


@pytest.mark.asyncio
async def test_overview_kpis_from_ingest() -> None:
    async with await _fresh_session() as s:
        await _ingest(
            s,
            [
                _msg(mid="k1", thread="T-k1",
                     demo={"intent": "urgent_truck", "sentiment": -0.3, "is_inbound": True, "broker": "x"}),
                _msg(mid="k2", thread="T-k2",
                     demo={"intent": "routine", "sentiment": 0.0, "is_inbound": True, "broker": "x"}),
            ],
        )
        k = await inbox_svc.overview_kpis(s)
        assert k.volume_7d == 2
        assert k.urgent == 1
