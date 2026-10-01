"""Call-list service — the operator's daily phone-queue reads/writes.

Thin business layer behind ``app/api/call_list.py``. The deterministic ranker
lives in ``app.pipeline.call_rank``; this service pumps the three inputs
(leads with phones, call outcomes, open capacity posts) and the per-lead
"last email" roll-up into it and persists outcomes.

Returns plain dataclasses; raises domain exceptions. Reusable from cron /
future queue workers because it never touches a Request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CallOutcome, CapacityPost, Lead, SentLog
from app.pipeline.call_rank import CALLBACK_DEFAULT_OFFSET_DAYS, CallRow, rank_call_list


ALLOWED_OUTCOMES = ("booked", "callback", "not_interested", "no_answer")


class LeadNotFoundError(Exception):
    """Raised when a lead id does not exist in the DB."""


@dataclass
class OutcomeHistoryRow:
    id: int
    outcome: str
    callback_at: str | None
    note: str | None
    logged_at: str
    logged_by: str | None


@dataclass
class OutcomeHistoryResult:
    lead_id: str
    items: list[OutcomeHistoryRow] = field(default_factory=list)


# ---------- shared read -----------------------------------------------------


async def load_and_rank(sessionmaker: Any, today: date, limit: int) -> list[CallRow]:
    """Pull inputs + email touchpoints, hand to the pure ranker.

    The ranker enforces the phone-required rule and the drop rules — this
    function is a dumb data pump so all interesting logic stays testable in
    isolation.
    """
    async with sessionmaker() as s:
        leads = (
            await s.execute(select(Lead).where(Lead.phone.isnot(None)).where(Lead.phone != ""))
        ).scalars().all()
        outcomes = (await s.execute(select(CallOutcome))).scalars().all()
        posts = (
            await s.execute(select(CapacityPost).where(CapacityPost.status == "open"))
        ).scalars().all()
        email_rows = (
            await s.execute(
                select(SentLog.lead_id, func.max(SentLog.sent_at)).group_by(SentLog.lead_id)
            )
        ).all()
    last_email_by_lead: dict[str, date] = {}
    for lid, sent_at in email_rows:
        if sent_at is None:
            continue
        d = sent_at.date() if isinstance(sent_at, datetime) else sent_at
        last_email_by_lead[lid] = d

    return rank_call_list(
        leads,
        outcomes,
        posts,
        today,
        top_n=limit,
        last_email_by_lead=last_email_by_lead,
    )


def today_utc() -> date:
    """UTC today — isolated so tests can monkeypatch.

    Kept UTC (not ET) because ``logged_at`` is stored as timestamptz; the
    overview surface uses ET on its own schedule.
    """
    return datetime.now(UTC).date()


async def log_outcome(
    sessionmaker: Any,
    *,
    lead_id: str,
    outcome: str,
    callback_at: date | None,
    note: str | None,
    today: date,
) -> list[CallRow]:
    """Insert a CallOutcome row and return the freshly ranked list (top 25).

    If the client omitted ``callback_at`` for a ``callback`` outcome, we
    default to ``today + CALLBACK_DEFAULT_OFFSET_DAYS`` so the ranker's
    callback-due rule stays consistent with what the route reports.
    """
    cb = callback_at
    if outcome == "callback" and cb is None:
        cb = today + timedelta(days=CALLBACK_DEFAULT_OFFSET_DAYS)

    async with sessionmaker() as s:
        exists = (
            await s.execute(select(Lead.id).where(Lead.id == lead_id))
        ).scalar_one_or_none()
        if not exists:
            raise LeadNotFoundError(lead_id)

        row = CallOutcome(
            lead_id=lead_id,
            outcome=outcome,
            callback_at=cb,
            note=note,
            logged_at=datetime.now(UTC),
        )
        s.add(row)
        await s.commit()

    return await load_and_rank(sessionmaker, today, 25)


async def get_history(
    session: AsyncSession, *, lead_id: str, limit: int = 50
) -> OutcomeHistoryResult:
    exists = (
        await session.execute(select(Lead.id).where(Lead.id == lead_id))
    ).scalar_one_or_none()
    if not exists:
        raise LeadNotFoundError(lead_id)
    rows = (
        (
            await session.execute(
                select(CallOutcome)
                .where(CallOutcome.lead_id == lead_id)
                .order_by(desc(CallOutcome.logged_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return OutcomeHistoryResult(
        lead_id=lead_id,
        items=[
            OutcomeHistoryRow(
                id=o.id,
                outcome=o.outcome,
                callback_at=o.callback_at.isoformat() if o.callback_at else None,
                note=o.note,
                logged_at=o.logged_at.isoformat() if o.logged_at else "",
                logged_by=o.logged_by,
            )
            for o in rows
        ],
    )
