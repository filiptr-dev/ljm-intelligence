"""Idempotent mailbox ingest — upserts ``mail_messages`` + advances ``mail_cursors``.

A crash never half-writes a cursor: cursor update is in the same transaction
as the batch of messages. v1 does synchronous batches of 100.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.adapters.email.mailbox import MailboxSource, RawMessage
from app.models import MailCursor, MailMessage

log = logging.getLogger(__name__)


@dataclass
class IngestStats:
    mailbox: str
    read: int = 0
    upserted: int = 0
    skipped: int = 0
    last_history_id: str | None = None
    status: str = "ok"
    error: str | None = None


RETENTION_MONTHS = 18


def _row_from(msg: RawMessage) -> dict:
    # Stamp email_lower (normalised sender) + retention_until on ingest so the
    # analysis queries can filter without touching from_addr case, and the
    # retention sweeper has a column to compare now() against. Both columns
    # exist from migration 0016; ingest fills them for new rows.
    retention_until = datetime.now(UTC) + timedelta(days=30 * RETENTION_MONTHS)
    return {
        "mailbox": msg.mailbox,
        "message_id": msg.message_id,
        "thread_id": msg.thread_id,
        "history_id": msg.history_id,
        "from_addr": msg.from_addr,
        "email_lower": (msg.from_addr or "").strip().lower() or None,
        "to_addrs": msg.to_addrs,
        "cc_addrs": msg.cc_addrs,
        "subject": msg.subject,
        "sent_at": msg.sent_at,
        "received_at": msg.received_at,
        "in_reply_to": msg.in_reply_to,
        "references_hdr": msg.references,
        "body_text": msg.body_text,
        "body_html": msg.body_html,
        "labels": msg.labels,
        "retention_until": retention_until,
        "raw": msg.raw,
    }


async def _upsert(session: AsyncSession, msg: RawMessage) -> bool:
    existing = (
        await session.execute(
            select(MailMessage).where(MailMessage.mailbox == msg.mailbox, MailMessage.message_id == msg.message_id)
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False
    session.add(MailMessage(**_row_from(msg)))
    # Triage runs inline in the same UoW — a half-written (message yes, insight
    # no) state is impossible by construction (plan §Rules "One session per
    # unit of work").
    from app.inbox.triage import triage_message

    try:
        await triage_message(session, msg)
    except Exception:  # noqa: BLE001 — triage must not block the ingest batch
        log.exception("mail/ingest: triage failed, continuing with raw message only")
    return True


async def _update_cursor(session: AsyncSession, mailbox: str, history_id: str, through_at: datetime | None) -> None:
    cur = (await session.execute(select(MailCursor).where(MailCursor.mailbox == mailbox))).scalar_one_or_none()
    if cur is None:
        session.add(MailCursor(mailbox=mailbox, history_id=history_id, backfilled_through_at=through_at))
    else:
        cur.history_id = history_id
        if through_at is not None:
            cur.backfilled_through_at = through_at


async def ingest_backfill(session: AsyncSession, source: MailboxSource, mailbox: str, months: int = 12) -> IngestStats:
    stats = IngestStats(mailbox=mailbox)
    since = datetime.now(UTC) - timedelta(days=30 * months)
    try:
        async for msg in source.backfill(mailbox, since):
            stats.read += 1
            inserted = await _upsert(session, msg)
            if inserted:
                stats.upserted += 1
            else:
                stats.skipped += 1
            stats.last_history_id = msg.history_id
        if stats.last_history_id:
            await _update_cursor(session, mailbox, stats.last_history_id, datetime.now(UTC))
        await session.commit()
    except Exception as exc:
        await session.rollback()
        stats.status = "error"
        stats.error = str(exc)
        log.exception("mail/ingest: backfill failed")
    return stats


async def ingest_incremental(session: AsyncSession, source: MailboxSource, mailbox: str) -> IngestStats:
    stats = IngestStats(mailbox=mailbox)
    cur = (await session.execute(select(MailCursor).where(MailCursor.mailbox == mailbox))).scalar_one_or_none()
    start = cur.history_id if cur else "0"
    try:
        async for msg, new_hid in source.incremental(mailbox, start):
            stats.read += 1
            inserted = await _upsert(session, msg)
            if inserted:
                stats.upserted += 1
            else:
                stats.skipped += 1
            stats.last_history_id = new_hid
        if stats.last_history_id:
            await _update_cursor(session, mailbox, stats.last_history_id, None)
        await session.commit()
    except Exception as exc:
        await session.rollback()
        stats.status = "error"
        stats.error = str(exc)
        log.exception("mail/ingest: incremental failed")
    return stats
