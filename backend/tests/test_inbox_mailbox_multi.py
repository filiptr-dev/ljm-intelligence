"""Multi-mailbox filter for the /emails + /messages chips UI.

The chips row posts repeated ``?mailbox=`` params (or a CSV); the repo-level
``_mb_where`` turns one value into ``col == v`` and a list into
``col IN (…)``. These tests guard the IN path for the two reads the pages
hit — list + sentiment + no-reply — plus the ``?mailbox=`` → single-filter
back-compat so the old URLs keep working.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

# Side-effect model imports — Base.metadata.create_all needs every mapped
# class loaded so cross-table FKs resolve (freight_runs → trucks). Pytest
# collection order isn't a reliable way to achieve that.
import app.fleet.models
import app.prospecting.models  # noqa: F401
from app.db import Base
from app.inbox import service as inbox_svc
from app.integrations.adapters.email.ingest import ingest_backfill
from app.integrations.adapters.email.mailbox import RawMessage, SimulatedMailbox

MAILBOXES = (
    "contact@ljminternational.com",
    "viktor@ljminternational.com",
    "mario@ljminternational.com",
)


def _msg(
    *,
    mid: str,
    mailbox: str,
    from_addr: str = "scott@chrobinson.com",
    sent_at: datetime | None = None,
    demo: dict | None = None,
) -> RawMessage:
    sent_at = sent_at or datetime.now(UTC) - timedelta(hours=1)
    raw = {"id": mid, "snippet": "demo"}
    if demo is not None:
        raw["demo"] = demo
    return RawMessage(
        message_id=mid, thread_id=f"T-{mid}", history_id="1",
        mailbox=mailbox, from_addr=from_addr, to_addrs=[mailbox], cc_addrs=[],
        subject="load", sent_at=sent_at, received_at=sent_at,
        in_reply_to=None, references=[], body_text="hi", body_html="<p>hi</p>",
        labels=["INBOX"], raw=raw,
    )


async def _fresh() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return maker()


async def _ingest(s: AsyncSession, msgs: list[RawMessage]) -> None:
    by_mbx: dict[str, list[RawMessage]] = {}
    for m in msgs:
        by_mbx.setdefault(m.mailbox, []).append(m)
    for mbx, mlist in by_mbx.items():
        await ingest_backfill(s, SimulatedMailbox(messages={mbx: mlist}), mbx, months=1)


@pytest.mark.asyncio
async def test_list_emails_in_filter_across_two_mailboxes() -> None:
    async with await _fresh() as s:
        msgs: list[RawMessage] = []
        for i, mbx in enumerate(MAILBOXES):
            msgs.append(_msg(
                mid=f"m{i}", mailbox=mbx,
                demo={"intent": "load_offer", "sentiment": 0.1, "is_inbound": True, "broker": "x"},
            ))
        await _ingest(s, msgs)
        # No filter → all three.
        _, total_all = await inbox_svc.list_emails(s, limit=10)
        assert total_all == 3
        # Single mailbox → one (back-compat `?mailbox=` path).
        _, total_one = await inbox_svc.list_emails(s, limit=10, mailbox=MAILBOXES[0])
        assert total_one == 1
        # Multi-mailbox (IN) → two.
        _, total_two = await inbox_svc.list_emails(
            s, limit=10, mailbox=[MAILBOXES[0], MAILBOXES[1]],
        )
        assert total_two == 2


@pytest.mark.asyncio
async def test_sentiment_buckets_in_filter() -> None:
    async with await _fresh() as s:
        msgs = [
            _msg(mid="a", mailbox=MAILBOXES[0],
                 demo={"intent": "praise", "sentiment": 0.6, "is_inbound": True, "broker": "x"}),
            _msg(mid="b", mailbox=MAILBOXES[1],
                 demo={"intent": "complaint", "sentiment": -0.5, "is_inbound": True, "broker": "x"}),
            _msg(mid="c", mailbox=MAILBOXES[2],
                 demo={"intent": "routine", "sentiment": 0.0, "is_inbound": True, "broker": "x"}),
        ]
        await _ingest(s, msgs)
        bucket = await inbox_svc.sentiment_buckets(
            s, mailbox=[MAILBOXES[0], MAILBOXES[1]],
        )
        assert bucket.inbound_total == 2
        assert bucket.positive == 1
        assert bucket.negative == 1
