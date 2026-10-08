"""Inbox signature widening: title sniff + inbound-reply domain match."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.db import Base
from app.inbox.triage import _maybe_extract_contact, _sniff_title
from app.prospecting.models import Lead, LeadContact


@dataclass
class _Msg:
    from_addr: str | None
    to_addrs: list[str] = field(default_factory=list)
    body_text: str = ""
    mailbox: str = "us@ljm.com"
    message_id: str = "m1"
    thread_id: str = "t1"
    subject: str = ""
    sent_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    raw: dict = field(default_factory=dict)


async def _fresh() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, expire_on_commit=False)()


def test_sniff_title_grabs_following_line() -> None:
    body = "Thanks,\nJane Smith\nDirector of Logistics\nAcme Shipping"
    assert _sniff_title(body, "Jane Smith") == "Director of Logistics"


def test_sniff_title_returns_none_when_no_match() -> None:
    assert _sniff_title("", "Jane") is None
    assert _sniff_title("some body", "Jane") is None


@pytest.mark.asyncio
async def test_attach_on_sender_domain_captures_title() -> None:
    s = await _fresh()
    s.add(Lead(id="MC-S", name="Acme", kind="Shipper", state="NJ", domain="acme.com"))
    await s.flush()
    msg = _Msg(from_addr="jane@acme.com", body_text="Thanks,\nJane Smith\nDirector of Logistics\nAcme")
    await _maybe_extract_contact(s, msg, "Jane Smith")
    await s.commit()
    row = (await s.execute(select(LeadContact).where(LeadContact.lead_id == "MC-S"))).scalar_one()
    assert row.email == "jane@acme.com"
    assert row.title == "Director of Logistics"


@pytest.mark.asyncio
async def test_attach_on_inbound_reply_domain() -> None:
    """We sent → reply came back from a different domain, but the recipient
    we sent *to* matches the lead's domain."""
    s = await _fresh()
    s.add(Lead(id="MC-R", name="Acme", kind="Shipper", state="NJ", domain="acme.com"))
    await s.flush()
    # Message is incoming from a random replier; our mailbox received it with
    # the lead's domain on the To addresses (an inbound reply after an outbound
    # thread we started).
    msg = _Msg(from_addr="jane@gmail.com", to_addrs=["someone@acme.com"], body_text="Thanks,\nJane\nShipping Manager")
    await _maybe_extract_contact(s, msg, "Jane")
    await s.commit()
    row = (await s.execute(select(LeadContact).where(LeadContact.lead_id == "MC-R"))).scalar_one_or_none()
    assert row is not None
    assert row.email == "jane@gmail.com"
