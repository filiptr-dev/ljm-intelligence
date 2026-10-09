"""Follow-ups — SQL seam.

Four read queries + one upsert. Each column's membership is mutually
exclusive by filter — see `service.py` for the precedence rule.
"""
from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import exists, func, not_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.followups.models import FollowupNote
from app.inbox.models import MailMessage
from app.outreach.models import CallOutcome, SentLog
from app.prospecting.models import Lead


# Column 1 — NEW: no outbound send, no logged call outcome.
async def read_new(s: AsyncSession, *, limit: int = 50) -> list[Lead]:
    has_send = select(SentLog.id).where(SentLog.lead_id == Lead.id).correlate(Lead)
    has_call = select(CallOutcome.id).where(CallOutcome.lead_id == Lead.id).correlate(Lead)
    stmt = (
        select(Lead)
        .where(not_(exists(has_send)))
        .where(not_(exists(has_call)))
        .order_by(Lead.fit_score.desc().nullslast(), Lead.last_seen_at.desc())
        .limit(limit)
    )
    return list((await s.execute(stmt)).scalars().all())


# Column 2 — CONTACTED: at least one send, no reply yet, not booked.
async def read_contacted(s: AsyncSession, *, limit: int = 50) -> list[tuple[Lead, datetime, int | None]]:
    """Returns (lead, last_sent_at, days_waiting_or_None)."""
    last_sent_sq = (
        select(
            SentLog.lead_id.label("lead_id"),
            func.max(SentLog.sent_at).label("last_sent_at"),
            func.max(SentLog.replied_at).label("last_replied_at"),
        )
        .group_by(SentLog.lead_id)
        .subquery()
    )
    has_inbound = select(MailMessage.message_id).where(
        MailMessage.thread_id == SentLog.thread_id,
        MailMessage.from_addr != "",
        MailMessage.from_addr != SentLog.to_email,
    )
    has_inbound_any = (
        select(SentLog.id)
        .where(SentLog.lead_id == Lead.id, exists(has_inbound))
        .correlate(Lead)
    )
    has_booked = (
        select(CallOutcome.id)
        .where(CallOutcome.lead_id == Lead.id, CallOutcome.outcome == "booked")
        .correlate(Lead)
    )
    stmt = (
        select(Lead, last_sent_sq.c.last_sent_at, last_sent_sq.c.last_replied_at)
        .join(last_sent_sq, last_sent_sq.c.lead_id == Lead.id)
        .where(last_sent_sq.c.last_replied_at.is_(None))
        .where(not_(exists(has_inbound_any)))
        .where(not_(exists(has_booked)))
        .order_by(last_sent_sq.c.last_sent_at.desc())
        .limit(limit)
    )
    rows = (await s.execute(stmt)).all()
    today = datetime.now(UTC)
    out: list[tuple[Lead, datetime, int | None]] = []
    for lead, sent_at, _ in rows:
        if sent_at is not None and sent_at.tzinfo is None:
            # SQLite returns naive timestamps; stamp UTC for the subtraction.
            sent_at = sent_at.replace(tzinfo=UTC)
        waiting = (today - sent_at).days if sent_at else None
        out.append((lead, sent_at, waiting))
    return out


# Column 3 — REPLIED: inbound reply or sent_log.replied_at, not booked.
async def read_replied(s: AsyncSession, *, limit: int = 50) -> list[tuple[Lead, datetime]]:
    replied_log = (
        select(
            SentLog.lead_id.label("lead_id"),
            func.max(SentLog.replied_at).label("replied_at"),
        )
        .where(SentLog.replied_at.is_not(None))
        .group_by(SentLog.lead_id)
        .subquery()
    )
    has_booked = (
        select(CallOutcome.id)
        .where(CallOutcome.lead_id == Lead.id, CallOutcome.outcome == "booked")
        .correlate(Lead)
    )
    stmt = (
        select(Lead, replied_log.c.replied_at)
        .join(replied_log, replied_log.c.lead_id == Lead.id)
        .where(not_(exists(has_booked)))
        .order_by(replied_log.c.replied_at.desc())
        .limit(limit)
    )
    return [(lead, ts) for lead, ts in (await s.execute(stmt)).all()]


# Column 4 — BOOKED: at least one call_outcome='booked'.
async def read_booked(s: AsyncSession, *, limit: int = 50) -> list[tuple[Lead, datetime]]:
    booked = (
        select(
            CallOutcome.lead_id.label("lead_id"),
            func.max(CallOutcome.logged_at).label("booked_at"),
        )
        .where(CallOutcome.outcome == "booked")
        .group_by(CallOutcome.lead_id)
        .subquery()
    )
    stmt = (
        select(Lead, booked.c.booked_at)
        .join(booked, booked.c.lead_id == Lead.id)
        .order_by(booked.c.booked_at.desc())
        .limit(limit)
    )
    return [(lead, ts) for lead, ts in (await s.execute(stmt)).all()]


async def read_notes(s: AsyncSession, lead_ids: list[str]) -> dict[str, FollowupNote]:
    if not lead_ids:
        return {}
    rows = (
        await s.execute(select(FollowupNote).where(FollowupNote.lead_id.in_(lead_ids)))
    ).scalars().all()
    return {r.lead_id: r for r in rows}


async def read_note(s: AsyncSession, lead_id: str) -> FollowupNote | None:
    return (
        await s.execute(select(FollowupNote).where(FollowupNote.lead_id == lead_id))
    ).scalar_one_or_none()


async def upsert_note(
    s: AsyncSession, *, lead_id: str, note: str, next_touch: date | None
) -> FollowupNote:
    existing = await read_note(s, lead_id)
    now = datetime.now(UTC)
    if existing is None:
        row = FollowupNote(
            lead_id=lead_id,
            note=note,
            next_touch=next_touch,
            updated_at=now,
        )
        s.add(row)
        await s.commit()
        await s.refresh(row)
        return row
    existing.note = note
    existing.next_touch = next_touch
    existing.updated_at = now
    await s.commit()
    await s.refresh(existing)
    return existing


async def lead_exists(s: AsyncSession, lead_id: str) -> bool:
    return (
        await s.execute(select(Lead.id).where(Lead.id == lead_id))
    ).scalar_one_or_none() is not None
