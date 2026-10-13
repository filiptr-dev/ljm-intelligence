"""Scoped tests for :func:`app.inbox.service.suggest_questions`.

Covers the key contract bits (AC1 / AC4 / AC5 / AC6):

* grounded path  — seeded inbound mail + insights + no_reply rows yield
                   chip strings that name the real brokers and counts.
* empty-mailbox  — zero rows yields an empty list, not an error.
* db-failure     — a session that raises never bubbles up; the function
                   returns ``[]``.
* cache hit      — a second call within the TTL returns the same list
                   object (no SQL re-executed), proving the in-process
                   cache contract from the plan.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register every model on Base.metadata
from app.db import Base
from app.inbox.models import MailMessage, MessageInsight, NoReplyTracker
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId, set_tenant

TENANT = TenantId(LJM_TENANT_ID)


@asynccontextmanager
async def _sessionmaker():
    set_tenant(TENANT)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        yield sm
    finally:
        await engine.dispose()


async def _seed(session: AsyncSession, mbx: str, now: datetime) -> None:
    """Two Acme rate-requests on CHI→DAL, one Bravo complaint, one open thread."""
    for i, when in enumerate([now - timedelta(days=2), now - timedelta(days=1)]):
        session.add(MailMessage(
            mailbox=mbx, message_id=f"m-acme-{i}", thread_id=f"t-acme-{i}",
            history_id="1", from_addr="ops@acme-logistics.com",
            email_lower="ops@acme-logistics.com", to_addrs=[mbx], cc_addrs=[],
            subject="rate", sent_at=when, received_at=when, body_text="x",
            body_html="", labels=[], raw={},
        ))
        session.add(MessageInsight(
            mailbox=mbx, message_id=f"m-acme-{i}", thread_id=f"t-acme-{i}",
            from_email_normalized="ops@acme-logistics.com",
            intent="rate_request", urgency="normal", sentiment=-0.4,
            confidence=0.9, lane_from="Chicago, IL", lane_to="Dallas, TX",
            broker_name="Acme Logistics",
        ))
    session.add(MailMessage(
        mailbox=mbx, message_id="m-bravo", thread_id="t-bravo",
        history_id="1", from_addr="dispatch@bravo-brokers.com",
        email_lower="dispatch@bravo-brokers.com", to_addrs=[mbx], cc_addrs=[],
        subject="late", sent_at=now - timedelta(days=3), received_at=now,
        body_text="x", body_html="", labels=[], raw={},
    ))
    session.add(MessageInsight(
        mailbox=mbx, message_id="m-bravo", thread_id="t-bravo",
        from_email_normalized="dispatch@bravo-brokers.com",
        intent="complaint", urgency="normal", sentiment=-0.5, confidence=0.9,
        broker_name="Bravo Brokers",
    ))
    session.add(NoReplyTracker(
        mailbox=mbx, thread_id="t-open-1",
        to_email_normalized="buyer@delta-shippers.com",
        subject="pending", we_sent_at=now - timedelta(days=4),
    ))
    await session.commit()


@pytest.mark.asyncio
async def test_suggest_questions_grounded() -> None:
    from app.inbox import service

    service._reset_suggestions_cache()
    mbx = "sales@ljm.com"
    now = datetime.now(UTC)

    async with _sessionmaker() as sm:
        async with sm() as s:
            await _seed(s, mbx, now)
        async with sm() as s:
            out = await service.suggest_questions(s, mailbox=mbx)

    assert len(out) >= 2, out
    text = " || ".join(x.text for x in out).lower()
    assert "acme" in text or "delta" in text or "bravo" in text, text
    assert "chicago" in text and "dallas" in text, text
    for s in out:
        assert any(ch.isdigit() for ch in s.text) or any(
            tok in s.text.lower() for tok in ("acme", "bravo", "delta")
        ), s.text


@pytest.mark.asyncio
async def test_suggest_questions_empty_mailbox() -> None:
    from app.inbox import service

    service._reset_suggestions_cache()
    async with _sessionmaker() as sm, sm() as s:
        out = await service.suggest_questions(s, mailbox="nobody@ljm.com")
    assert out == []


@pytest.mark.asyncio
async def test_suggest_questions_db_failure_never_raises() -> None:
    from app.inbox import service

    service._reset_suggestions_cache()

    class _Boom:
        async def execute(self, *a, **k):
            raise RuntimeError("db down")

    out = await service.suggest_questions(_Boom(), mailbox="x@ljm.com")  # type: ignore[arg-type]
    assert out == []


@pytest.mark.asyncio
async def test_suggest_questions_cache_hit_skips_db() -> None:
    """Second call inside the TTL must not re-query (AC6)."""
    from app.inbox import service

    service._reset_suggestions_cache()
    mbx = "cache@ljm.com"
    now = datetime.now(UTC)

    async with _sessionmaker() as sm:
        async with sm() as s:
            await _seed(s, mbx, now)
        async with sm() as s:
            first = await service.suggest_questions(s, mailbox=mbx)

    class _Boom:
        async def execute(self, *a, **k):
            raise AssertionError("cache miss — DB was hit on second call")

    second = await service.suggest_questions(_Boom(), mailbox=mbx)  # type: ignore[arg-type]
    assert second is first
    assert len(second) >= 1


