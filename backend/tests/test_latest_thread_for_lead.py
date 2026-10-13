"""Tests for app.inbox.service.latest_thread_for_lead."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.lead_ai_summary_service import LeadNotFound
from app.db import Base
from app.inbox import service as inbox_svc
from app.models import Lead, LeadContact, MailMessage

pytestmark = pytest.mark.asyncio


async def _sm():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(e, expire_on_commit=False)


def _mm(mid: str, email: str, *, hours_ago: float, domain: str | None = None) -> MailMessage:
    recv = datetime.now(UTC) - timedelta(hours=hours_ago)
    return MailMessage(
        mailbox="owner@ljm.com",
        message_id=mid,
        thread_id=mid,
        history_id="1",
        from_addr=email,
        email_lower=email.lower(),
        email_domain=domain,
        subject="Re: load",
        body_text="hi",
        sent_at=recv,
        received_at=recv,
    )


async def test_unknown_lead_raises():
    sm = await _sm()
    async with sm() as s:
        with pytest.raises(LeadNotFound):
            await inbox_svc.latest_thread_for_lead(s, "nope")


async def test_no_mail_returns_none():
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ", primary_email="a@acme.com"))
        await s.commit()
        assert await inbox_svc.latest_thread_for_lead(s, "A") is None


async def test_latest_wins_by_received_at():
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ", primary_email="a@acme.com"))
        s.add(_mm("old", "a@acme.com", hours_ago=10))
        s.add(_mm("new", "a@acme.com", hours_ago=1))
        await s.commit()
        assert await inbox_svc.latest_thread_for_lead(s, "A") == "new"


async def test_matches_contact_email():
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ", primary_email="a@acme.com"))
        s.add(LeadContact(lead_id="A", email="scott@acme.com"))
        s.add(_mm("t1", "scott@acme.com", hours_ago=2))
        await s.commit()
        assert await inbox_svc.latest_thread_for_lead(s, "A") == "t1"


async def test_matches_company_domain_not_free_mail():
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ",
                   primary_email="a@acme.com", domain="acme.com"))
        # No prior contact for ops@acme.com, but domain match should pick it up.
        s.add(_mm("t1", "ops@acme.com", hours_ago=1, domain="acme.com"))
        await s.commit()
        assert await inbox_svc.latest_thread_for_lead(s, "A") == "t1"


async def test_other_lead_mail_is_ignored():
    sm = await _sm()
    async with sm() as s:
        s.add(Lead(id="A", name="A", kind="Broker", state="NJ", primary_email="a@acme.com"))
        s.add(Lead(id="B", name="B", kind="Broker", state="NJ", primary_email="b@beta.com"))
        s.add(_mm("tA", "a@acme.com", hours_ago=5))
        s.add(_mm("tB", "b@beta.com", hours_ago=1))  # more recent, but B's
        await s.commit()
        assert await inbox_svc.latest_thread_for_lead(s, "A") == "tA"
