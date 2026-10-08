"""Inbox — repository seam.

All SELECT calls for inbox-owned aggregates (MailMessage, MessageInsight,
NoReplyTracker) + the cross-module read of identity.SettingsRow for the
unsubscribe-link render path. Previously inlined in `service.py`; the
17 call sites are now function calls into this module. See the plan:
projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.

Why so many thin functions and not a single Repo class: the service's
public functions each read a focused shape (``list_emails``, ``triage_list``,
etc.). A one-row-per-use-case extraction keeps the service's call sites
one line each — easy to read, easy to unit-test with an in-memory fake.
"""
from __future__ import annotations

from typing import Any, Protocol  # noqa: F401

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.models import MailMessage, MessageInsight, NoReplyTracker


def _inbound_filter():
    return MailMessage.from_addr != MailMessage.mailbox


def _emails_base(intent: str | None, text_query: str | None):
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    base = (
        select(
            m.message_id, m.mailbox, m.thread_id, m.from_addr, m.to_addrs, m.subject,
            m.body_text, m.sent_at,
            j.intent, j.urgency, j.sentiment, j.confidence, j.rate_usd, j.lane_from,
            j.lane_to, j.broker_name, j.evidence,
        )
        .select_from(MailMessage.__table__.join(
            MessageInsight.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        ))
    )
    if intent:
        base = base.where(j.intent == intent)
    if text_query:
        q = f"%{text_query.lower()}%"
        base = base.where(func.lower(m.subject + " " + m.body_text).like(q))
    return base, m


async def count_emails(session: AsyncSession, *, intent, text_query) -> int:
    base, _ = _emails_base(intent, text_query)
    return int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())


async def fetch_emails_page(session: AsyncSession, *, intent, text_query, offset: int, limit: int) -> list[Any]:
    base, m = _emails_base(intent, text_query)
    rows_q = base.order_by(m.sent_at.desc()).offset(offset).limit(limit)
    return list((await session.execute(rows_q)).all())


async def fetch_intent_counts(session: AsyncSession) -> list[Any]:
    return list((
        await session.execute(
            select(MessageInsight.intent, func.count())
            .group_by(MessageInsight.intent)
            .order_by(func.count().desc())
        )
    ).all())


async def fetch_inbound_sentiments(session: AsyncSession) -> list[Any]:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    base = select(j.sentiment).select_from(
        MessageInsight.__table__.join(
            MailMessage.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        )
    ).where(_inbound_filter())
    return list((await session.execute(base)).all())


async def fetch_triage_rows(session: AsyncSession, *, urgent_only: bool, limit: int) -> list[Any]:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    stmt = (
        select(
            m.message_id, m.mailbox, m.thread_id, m.from_addr, m.subject, m.sent_at, m.body_text,
            j.intent, j.urgency, j.sentiment, j.broker_name,
        )
        .select_from(MailMessage.__table__.join(
            MessageInsight.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        ))
        .where(_inbound_filter())
    )
    if urgent_only:
        stmt = stmt.where(j.urgency == "urgent")
    stmt = stmt.order_by(m.sent_at.desc()).limit(limit * 2 if not urgent_only else limit)
    return list((await session.execute(stmt)).all())


async def fetch_no_reply_rows(session: AsyncSession, *, limit: int) -> list[NoReplyTracker]:
    rows = (
        await session.execute(
            select(NoReplyTracker).order_by(NoReplyTracker.we_sent_at.asc()).limit(limit)
        )
    ).scalars().all()
    return list(rows)


async def fetch_response_time_rows(session: AsyncSession, *, broker_domain: str | None) -> list[Any]:
    stmt = select(
        MailMessage.thread_id, MailMessage.from_addr, MailMessage.mailbox,
        MailMessage.sent_at, MailMessage.email_lower,
    ).order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
    if broker_domain:
        stmt = stmt.where(MailMessage.email_lower.ilike(f"%@{broker_domain}"))
    return list((await session.execute(stmt)).all())


async def fetch_staff_rows(session: AsyncSession) -> list[Any]:
    return list((
        await session.execute(
            select(MailMessage.mailbox, MailMessage.from_addr, MailMessage.thread_id, MailMessage.sent_at)
            .order_by(MailMessage.mailbox, MailMessage.thread_id, MailMessage.sent_at)
        )
    ).all())


async def count_all_messages(session: AsyncSession) -> int:
    m = MailMessage.__table__.c
    return int((
        await session.execute(select(func.count(m.message_id)).select_from(MailMessage.__table__))
    ).scalar_one())


async def count_no_reply_rows(session: AsyncSession) -> int:
    return int((
        await session.execute(select(func.count()).select_from(NoReplyTracker.__table__))
    ).scalar_one())


async def count_urgent_insights(session: AsyncSession) -> int:
    j = MessageInsight.__table__.c
    return int((
        await session.execute(
            select(func.count()).select_from(MessageInsight.__table__).where(j.urgency == "urgent")
        )
    ).scalar_one())


async def count_negative_insights(session: AsyncSession) -> int:
    j = MessageInsight.__table__.c
    return int((
        await session.execute(
            select(func.count()).select_from(MessageInsight.__table__).where(j.sentiment < -0.1)
        )
    ).scalar_one())


async def fetch_relationship_rows(session: AsyncSession, *, broker_domain: str) -> list[Any]:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    return list((
        await session.execute(
            select(j.intent, j.sentiment, j.broker_name, m.sent_at, m.from_addr, m.mailbox)
            .select_from(MessageInsight.__table__.join(
                MailMessage.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(m.email_lower.ilike(f"%@{broker_domain}"))
        )
    ).all())


async def fetch_thread_rows(session: AsyncSession, *, thread_id: str) -> list[Any]:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    return list((
        await session.execute(
            select(
                m.message_id, m.mailbox, m.thread_id, m.from_addr, m.to_addrs, m.subject,
                m.body_text, m.sent_at,
                j.intent, j.urgency, j.sentiment, j.confidence, j.rate_usd, j.lane_from,
                j.lane_to, j.broker_name, j.evidence,
            )
            .select_from(MailMessage.__table__.outerjoin(
                MessageInsight.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(m.thread_id == thread_id)
            .order_by(m.sent_at.asc())
        )
    ).all())


async def fetch_status_board_rows(session: AsyncSession) -> list[Any]:
    return list((
        await session.execute(
            select(
                MailMessage.thread_id, MailMessage.mailbox, MailMessage.subject,
                MailMessage.from_addr, MailMessage.to_addrs, MailMessage.sent_at,
            )
            .order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
        )
    ).all())


async def get_settings_row(session: AsyncSession) -> Any:
    """Cross-module read: identity.SettingsRow needed by _render_and_wrap.

    Kept in inbox/repository.py (and NOT in identity/) because this is a
    read-only Queries/Public call from inbox — writes still belong to
    identity. Imported inside the function so the module doesn't carry a
    hard import-time dependency on identity.
    """
    from app.identity.models import SettingsRow

    return (
        await session.execute(select(SettingsRow).where(SettingsRow.id == 1))
    ).scalar_one_or_none()


async def find_contact_id_by_email(session: AsyncSession, *, email: str) -> int | None:
    from app.prospecting.models import LeadContact

    return (
        await session.execute(
            select(LeadContact.id).where(LeadContact.email == email).limit(1)
        )
    ).scalar_one_or_none()


# ---- writes (DIP: writes still go through the repo seam) ------------------

from sqlalchemy import delete as _delete, func as _func


async def delete_no_reply_for_thread(session: AsyncSession, *, thread_id: str) -> None:
    await session.execute(_delete(NoReplyTracker).where(NoReplyTracker.thread_id == thread_id))


async def delete_messages_by_email(session: AsyncSession, *, email: str) -> int:
    result = await session.execute(
        _delete(MailMessage).where(
            (MailMessage.email_lower == email)
            | (_func.lower(MailMessage.from_addr) == email)
        )
    )
    return int(result.rowcount or 0)


async def delete_insights_by_email(session: AsyncSession, *, email: str) -> int:
    result = await session.execute(
        _delete(MessageInsight).where(MessageInsight.from_email_normalized == email)
    )
    return int(result.rowcount or 0)


async def delete_no_reply_by_email(session: AsyncSession, *, email: str) -> int:
    result = await session.execute(
        _delete(NoReplyTracker).where(NoReplyTracker.to_email_normalized == email)
    )
    return int(result.rowcount or 0)


async def delete_messages_past_retention(session: AsyncSession, *, now) -> int:
    result = await session.execute(
        _delete(MailMessage).where(
            MailMessage.retention_until.isnot(None),
            MailMessage.retention_until < now,
        )
    )
    return int(result.rowcount or 0)
