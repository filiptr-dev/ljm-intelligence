"""Analysis predictions — nightly fan-out + read endpoints.

Scope: the mechanics the plan's Step 6 + "further analyses" call for.
  * run_nightly() produces broker + lane predictions deterministically.
  * forget_contact deletes everything + writes audit row.
  * status_board stages threads correctly.
  * retention_sweep removes expired rows.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import service as analysis_svc
from app.analysis.models import (
    BrokerLookalike,
    BrokerPrediction,
    ForgetContactAudit,
    LanePrediction,
    ObjectionCluster,
    PredictionRun,
)
from app.db import Base
from app.inbox import service as inbox_svc
from app.inbox.models import MailMessage, MessageInsight, NoReplyTracker


async def _fresh_session() -> AsyncSession:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    return maker()


async def _seed(session: AsyncSession) -> None:
    """Seed a tiny deterministic corpus — 2 brokers across 2 lanes, enough
    for every prediction to produce a row.
    """
    now = datetime.now(UTC)
    MAILBOX = "contact@ljminternational.com"
    rows = [
        # Broker A — chrobinson.com — load offer + booked
        ("m1", "T1", "scott@chrobinson.com", "LA, CA", "Dallas, TX", "load_offer", 0.1, 2200.0, now - timedelta(days=10)),
        ("m2", "T1", MAILBOX, None, None, None, None, None, now - timedelta(days=10, hours=-1)),
        ("m3", "T1", "scott@chrobinson.com", "LA, CA", "Dallas, TX", "booked", 0.4, 2200.0, now - timedelta(days=9)),
        # Broker A again, same lane — a complaint
        ("m4", "T2", "scott@chrobinson.com", "LA, CA", "Dallas, TX", "complaint", -0.6, None, now - timedelta(days=8)),
        # Broker B — chempower.com — a load offer + rate_request
        ("m5", "T3", "deb@chempower.com", "LA, CA", "Dallas, TX", "load_offer", 0.2, 2300.0, now - timedelta(days=5)),
        ("m6", "T3", MAILBOX, None, None, None, None, None, now - timedelta(days=5, hours=-2)),
        ("m7", "T4", "deb@chempower.com", "NYC, NY", "Miami, FL", "rate_request", 0.1, None, now - timedelta(days=3)),
        # Broker A payment
        ("m8", "T5", "ap@chrobinson.com", None, None, "payment", -0.3, None, now - timedelta(days=2)),
        ("m9", "T6", "ap@chrobinson.com", None, None, "payment", -0.3, None, now - timedelta(days=1)),
    ]
    for mid, tid, frm, lf, lt, intent, sent, rate, when in rows:
        direction_in = frm != MAILBOX
        session.add(MailMessage(
            mailbox=MAILBOX, message_id=mid, thread_id=tid, history_id="1",
            from_addr=frm, email_lower=frm.lower(), to_addrs=[MAILBOX if direction_in else "to@x.com"],
            cc_addrs=[], subject=f"S {mid}", sent_at=when, received_at=when,
            in_reply_to=None, references_hdr=[], body_text="hi", body_html="",
            labels=[], retention_until=now + timedelta(days=365), raw={},
        ))
        if intent is not None:
            session.add(MessageInsight(
                mailbox=MAILBOX, message_id=mid, thread_id=tid,
                from_email_normalized=frm.lower(),
                intent=intent, urgency="normal", sentiment=sent or 0.0, confidence=0.9,
                rate_usd=rate, lane_from=lf, lane_to=lt, equipment="Van",
                broker_name=frm.split("@", 1)[0].title(),
                evidence="rate too high — need cheaper" if intent == "complaint" else None,
                model="demo", model_version="demo-v1",
            ))
    await session.commit()


@pytest.mark.asyncio
async def test_run_nightly_produces_predictions() -> None:
    async with await _fresh_session() as s:
        await _seed(s)
        stats = await analysis_svc.run_nightly(s)
        await s.commit()
        assert stats["brokers"] >= 2
        assert stats["lanes"] >= 1

        brokers = (await s.execute(select(BrokerPrediction))).scalars().all()
        lanes = (await s.execute(select(LanePrediction))).scalars().all()
        runs = (await s.execute(select(PredictionRun))).scalars().all()
        assert len(runs) == 1 and runs[0].status == "ok"
        domains = {b.broker_domain for b in brokers}
        assert "chrobinson.com" in domains
        assert "chempower.com" in domains
        assert all(0 <= b.health_score <= 100 for b in brokers)
        # slow-payer: chrobinson has >=2 payment insights
        chr = next(b for b in brokers if b.broker_domain == "chrobinson.com")
        assert chr.is_slow_payer is True
        # a chrobinson win: m3 booked ⇒ win_prob > 0
        assert chr.win_probability > 0
        # at least one lane with sample_size >= 1
        assert any(l.sample_size >= 1 for l in lanes)


@pytest.mark.asyncio
async def test_run_nightly_is_idempotent() -> None:
    async with await _fresh_session() as s:
        await _seed(s)
        await analysis_svc.run_nightly(s)
        await s.commit()
        n1 = (await s.execute(select(BrokerPrediction))).scalars().all()
        await analysis_svc.run_nightly(s)
        await s.commit()
        n2 = (await s.execute(select(BrokerPrediction))).scalars().all()
        assert len(n1) == len(n2)


@pytest.mark.asyncio
async def test_forget_contact_wipes_and_audits() -> None:
    async with await _fresh_session() as s:
        await _seed(s)
        result = await inbox_svc.forget_contact(s, email="scott@chrobinson.com", performed_by="owner@ljm")
        await s.commit()
        assert result["ok"] is True
        remaining = (await s.execute(
            select(MailMessage).where(MailMessage.email_lower == "scott@chrobinson.com")
        )).scalars().all()
        assert remaining == []
        audit = (await s.execute(select(ForgetContactAudit))).scalars().all()
        assert len(audit) == 1
        assert audit[0].performed_by == "owner@ljm"
        assert audit[0].messages_deleted >= 1


@pytest.mark.asyncio
async def test_retention_sweep_drops_expired() -> None:
    async with await _fresh_session() as s:
        now = datetime.now(UTC)
        s.add(MailMessage(
            mailbox="x@ljminternational.com", message_id="old", thread_id="T",
            history_id="1", from_addr="who@x.com", email_lower="who@x.com",
            to_addrs=[], cc_addrs=[], subject="old", sent_at=now - timedelta(days=1000),
            received_at=now - timedelta(days=1000), in_reply_to=None, references_hdr=[],
            body_text="", body_html="", labels=[],
            retention_until=now - timedelta(days=1), raw={},
        ))
        s.add(MailMessage(
            mailbox="x@ljminternational.com", message_id="new", thread_id="T",
            history_id="1", from_addr="who@x.com", email_lower="who@x.com",
            to_addrs=[], cc_addrs=[], subject="new", sent_at=now,
            received_at=now, in_reply_to=None, references_hdr=[],
            body_text="", body_html="", labels=[],
            retention_until=now + timedelta(days=365), raw={},
        ))
        await s.commit()
        result = await inbox_svc.retention_sweep(s)
        await s.commit()
        assert result["deleted"] == 1
        rows = (await s.execute(select(MailMessage))).scalars().all()
        assert [r.message_id for r in rows] == ["new"]


@pytest.mark.asyncio
async def test_status_board_stages_threads() -> None:
    async with await _fresh_session() as s:
        await _seed(s)
        rows = await inbox_svc.status_board(s)
        stages = {r.stage for r in rows}
        # must contain at least waiting_on_us (we got inbound-last threads).
        assert "waiting_on_us" in stages


@pytest.mark.asyncio
async def test_ai_draft_pre_fills_from_last_inbound() -> None:
    async with await _fresh_session() as s:
        await _seed(s)
        draft = await inbox_svc.ai_draft_reply(s, "T1")
        assert draft is not None
        assert draft.subject.startswith("Re:")
        assert len(draft.body_text) > 20
        assert "<p>" in draft.body_html
