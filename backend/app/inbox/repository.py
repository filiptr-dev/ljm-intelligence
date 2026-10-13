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


# Accept a single mailbox string, a list of mailboxes, or None (no filter).
# One helper keeps the SQL in every mailbox-aware query consistent: single →
# ``col == mb``, multi → ``col IN (…)``. The multi path lets the /emails and
# /messages pages narrow by any subset of the operator's allowlisted boxes
# (see the chips UI in ``mailbox-selector.tsx``) without each repository
# function having to grow a second kwarg.
Mailboxes = str | list[str] | tuple[str, ...] | None


def _mb_where(col, mailbox: Mailboxes):
    if mailbox is None:
        return None
    if isinstance(mailbox, str):
        if not mailbox:
            return None
        return col == mailbox
    values = [m for m in mailbox if m]
    if not values:
        return None
    if len(values) == 1:
        return col == values[0]
    return col.in_(values)


def _emails_base(intent: str | None, text_query: str | None, mailbox: Mailboxes = None):
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
    cond = _mb_where(m.mailbox, mailbox)
    if cond is not None:
        base = base.where(cond)
    return base, m


async def count_emails(session: AsyncSession, *, intent, text_query, mailbox: Mailboxes = None) -> int:
    base, _ = _emails_base(intent, text_query, mailbox)
    return int((await session.execute(select(func.count()).select_from(base.subquery()))).scalar_one())


async def fetch_emails_page(
    session: AsyncSession, *, intent, text_query, offset: int, limit: int, mailbox: Mailboxes = None,
) -> list[Any]:
    base, m = _emails_base(intent, text_query, mailbox)
    rows_q = base.order_by(m.sent_at.desc()).offset(offset).limit(limit)
    return list((await session.execute(rows_q)).all())


async def fetch_intent_counts(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[Any]:
    # When scoped by mailbox we must join to MailMessage — MessageInsight carries
    # its own ``mailbox`` column but we align with the write-side rule that
    # list_emails joins on (mailbox,message_id,tenant_id).
    if mailbox:
        j = MessageInsight.__table__.c
        m = MailMessage.__table__.c
        stmt = (
            select(j.intent, func.count())
            .select_from(MessageInsight.__table__.join(
                MailMessage.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(_mb_where(m.mailbox, mailbox))
            .group_by(j.intent)
            .order_by(func.count().desc())
        )
        return list((await session.execute(stmt)).all())
    return list((
        await session.execute(
            select(MessageInsight.intent, func.count())
            .group_by(MessageInsight.intent)
            .order_by(func.count().desc())
        )
    ).all())


async def fetch_inbound_sentiments(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[Any]:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    base = select(j.sentiment).select_from(
        MessageInsight.__table__.join(
            MailMessage.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        )
    ).where(_inbound_filter())
    cond = _mb_where(m.mailbox, mailbox)
    if cond is not None:
        base = base.where(cond)
    return list((await session.execute(base)).all())


async def fetch_triage_rows(
    session: AsyncSession, *, urgent_only: bool, limit: int, mailbox: Mailboxes = None,
) -> list[Any]:
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
    if mailbox:
        stmt = stmt.where(_mb_where(m.mailbox, mailbox))
    stmt = stmt.order_by(m.sent_at.desc()).limit(limit * 2 if not urgent_only else limit)
    return list((await session.execute(stmt)).all())


async def fetch_no_reply_rows(
    session: AsyncSession, *, limit: int, mailbox: Mailboxes = None,
) -> list[NoReplyTracker]:
    stmt = select(NoReplyTracker).order_by(NoReplyTracker.we_sent_at.asc())
    if mailbox:
        stmt = stmt.where(_mb_where(NoReplyTracker.mailbox, mailbox))
    rows = (await session.execute(stmt.limit(limit))).scalars().all()
    return list(rows)


async def fetch_response_time_rows(session: AsyncSession, *, broker_domain: str | None) -> list[Any]:
    stmt = select(
        MailMessage.thread_id, MailMessage.from_addr, MailMessage.mailbox,
        MailMessage.sent_at, MailMessage.email_lower,
    ).order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
    if broker_domain:
        stmt = stmt.where(MailMessage.email_lower.ilike(f"%@{broker_domain}"))
    return list((await session.execute(stmt)).all())


async def fetch_staff_rows(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[Any]:
    stmt = (
        select(MailMessage.mailbox, MailMessage.from_addr, MailMessage.thread_id, MailMessage.sent_at)
        .order_by(MailMessage.mailbox, MailMessage.thread_id, MailMessage.sent_at)
    )
    if mailbox:
        stmt = stmt.where(_mb_where(MailMessage.mailbox, mailbox))
    return list((await session.execute(stmt)).all())


async def count_all_messages(session: AsyncSession, *, mailbox: Mailboxes = None) -> int:
    m = MailMessage.__table__.c
    stmt = select(func.count(m.message_id)).select_from(MailMessage.__table__)
    if mailbox:
        stmt = stmt.where(_mb_where(m.mailbox, mailbox))
    return int((await session.execute(stmt)).scalar_one())


async def count_no_reply_rows(session: AsyncSession, *, mailbox: Mailboxes = None) -> int:
    stmt = select(func.count()).select_from(NoReplyTracker.__table__)
    if mailbox:
        stmt = stmt.where(_mb_where(NoReplyTracker.__table__.c.mailbox, mailbox))
    return int((await session.execute(stmt)).scalar_one())


async def count_urgent_insights(session: AsyncSession, *, mailbox: Mailboxes = None) -> int:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    if mailbox:
        stmt = (
            select(func.count())
            .select_from(MessageInsight.__table__.join(
                MailMessage.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(j.urgency == "urgent")
            .where(_mb_where(m.mailbox, mailbox))
        )
    else:
        stmt = select(func.count()).select_from(MessageInsight.__table__).where(j.urgency == "urgent")
    return int((await session.execute(stmt)).scalar_one())


async def count_negative_insights(session: AsyncSession, *, mailbox: Mailboxes = None) -> int:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    if mailbox:
        stmt = (
            select(func.count())
            .select_from(MessageInsight.__table__.join(
                MailMessage.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(j.sentiment < -0.1)
            .where(_mb_where(m.mailbox, mailbox))
        )
    else:
        stmt = select(func.count()).select_from(MessageInsight.__table__).where(j.sentiment < -0.1)
    return int((await session.execute(stmt)).scalar_one())


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


async def fetch_status_board_rows(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[Any]:
    stmt = (
        select(
            MailMessage.thread_id, MailMessage.mailbox, MailMessage.subject,
            MailMessage.from_addr, MailMessage.to_addrs, MailMessage.sent_at,
        )
        .order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
    )
    if mailbox:
        stmt = stmt.where(_mb_where(MailMessage.mailbox, mailbox))
    return list((await session.execute(stmt)).all())


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
