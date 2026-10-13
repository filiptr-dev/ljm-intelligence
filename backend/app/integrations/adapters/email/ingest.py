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

from app.inbox.models import MailCursor, MailMessage
from app.integrations.adapters.email.mailbox import MailboxSource, RawMessage
from app.integrations.adapters.email.sanitize import compact_bodies

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
    # Sanitize + compact at ingest so stored bytes are reader-safe (no
    # <script>/tracking-pixel), QP-decoded, and html-only messages still
    # have a non-empty ``body_text`` for triage reads downstream.
    body_text_clean, body_html_clean = compact_bodies(msg.body_text, msg.body_html)
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
        "body_text": body_text_clean,
        "body_html": body_html_clean,
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

    outcome = None
    try:
        outcome = await triage_message(session, msg)
    except Exception:
        log.exception("mail/ingest: triage failed, continuing with raw message only")
    # Bell notification — tail of the same UoW. Swallowed on failure so a
    # notifications bug can never stop mail from landing. 24h recency gate
    # inside the service keeps backfill traffic off the bell.
    if outcome is not None:
        try:
            from app.inbox.notifications_service import create_from_message

            await create_from_message(
                session, msg, intent=outcome.intent, urgency=outcome.urgency,
            )
        except Exception:
            log.exception(
                "mail/ingest: notification write failed, continuing"
            )
    return True


def _hid_int(s: str | None) -> int:
    try:
        return int(s or 0)
    except (TypeError, ValueError):
        return 0


async def _update_cursor(
    session: AsyncSession,
    mailbox: str,
    history_id: str,
    through_at: datetime | None,
) -> None:
    """Advance the mailbox cursor, monotonically.

    ``history_id`` is only ever moved forward (``int(new) > int(existing)``).
    Yesterday's bug: ``ingest_backfill`` wrote the OLDEST message's
    ``history_id`` as the last assignment of its loop, stomping the live
    incremental cursor to a value from months ago and forcing a silent
    full re-replay the next tick. The guard here is the fix.

    ``backfilled_through_at`` is written whenever a non-None value is
    passed; the gradual-import step writes the OLDER edge of the window
    it just walked, so this column marches backwards by design.
    """
    cur = (
        await session.execute(select(MailCursor).where(MailCursor.mailbox == mailbox))
    ).scalar_one_or_none()
    if cur is None:
        session.add(
            MailCursor(
                mailbox=mailbox,
                history_id=history_id,
                backfilled_through_at=through_at,
            )
        )
        return
    if _hid_int(history_id) > _hid_int(cur.history_id):
        cur.history_id = history_id
    if through_at is not None:
        cur.backfilled_through_at = through_at


BACKFILL_COMMIT_BATCH = 100


async def ingest_backfill(
    session: AsyncSession,
    source: MailboxSource,
    mailbox: str,
    months: int = 12,
    days: int | None = None,
    hours: int | None = None,
    window: tuple[datetime, datetime] | None = None,
) -> IngestStats:
    """Backfill a mailbox.

    Window selection — finest wins: ``window`` (``older_than, newer_than``,
    used by the gradual-import step) > ``hours`` > ``days`` > ``months``.
    When ``window`` is provided the simulated source ignores the upper
    bound (its tests use ``since`` only); the Gmail source uses
    ``before:/after:`` to narrow the query.

    Transaction model: commits every ``BACKFILL_COMMIT_BATCH`` upserts and
    once more at the end for the tail. Single-transaction backfills held
    row locks and blocked ``ALTER TABLE`` during the day's migration;
    these batched commits remove that pressure. ``_update_cursor`` is
    monotone, so repeated commits never roll the live incremental cursor
    backwards.
    """
    stats = IngestStats(mailbox=mailbox)
    if window is not None:
        older_than, _newer_than = window
        since = older_than
    elif hours is not None and hours > 0:
        since = datetime.now(UTC) - timedelta(hours=hours)
    elif days is not None and days > 0:
        since = datetime.now(UTC) - timedelta(days=days)
    else:
        since = datetime.now(UTC) - timedelta(days=30 * months)

    # Track monotone max separately from the per-message last_history_id
    # that the stats row reports.
    max_hid = 0
    uncommitted = 0
    try:
        async for msg in source.backfill(mailbox, since, window=window):
            stats.read += 1
            inserted = await _upsert(session, msg)
            if inserted:
                stats.upserted += 1
            else:
                stats.skipped += 1
            stats.last_history_id = msg.history_id
            try:
                hid_i = int(msg.history_id or 0)
            except (TypeError, ValueError):
                hid_i = 0
            max_hid = max(max_hid, hid_i)
            uncommitted += 1
            if uncommitted >= BACKFILL_COMMIT_BATCH:
                # Mid-window commit: do NOT move backfilled_through_at yet.
                # Only the tail commit (window fully walked) may claim the
                # older edge, otherwise a later failure leaves a hole the
                # cursor says is covered.
                await _update_cursor(
                    session, mailbox, str(max_hid) if max_hid else "0", None,
                )
                await session.commit()
                log.info(
                    "mail/ingest: backfill batch committed",
                    extra={"mailbox": mailbox, "committed": uncommitted},
                )
                uncommitted = 0
        # Tail commit.
        await _update_cursor(
            session, mailbox, str(max_hid) if max_hid else "0",
            _window_through(window) or datetime.now(UTC),
        )
        await session.commit()
    except Exception as exc:
        await session.rollback()
        stats.status = "error"
        stats.error = str(exc)
        log.exception("mail/ingest: backfill failed")
    return stats


def _window_through(window: tuple[datetime, datetime] | None) -> datetime | None:
    """When the caller supplied an explicit window, write its OLDER edge
    as ``backfilled_through_at`` — the gradual-import step marches this
    pointer backwards every invocation."""
    if window is None:
        return None
    older_than, _newer_than = window
    return older_than




async def ingest_incremental(session: AsyncSession, source: MailboxSource, mailbox: str) -> IngestStats:
    stats = IngestStats(mailbox=mailbox)
    cur = (await session.execute(select(MailCursor).where(MailCursor.mailbox == mailbox))).scalar_one_or_none()
    start = cur.history_id if cur else "0"
    try:
        async for msg, new_hid in source.incremental(mailbox, start):
            # Empty-page marker: the source observed a higher historyId on
            # the page but there was no ``messagesAdded`` entry to upsert.
            # Advance the cursor anyway so Gmail's ~7-day history retention
            # does not march past us and silently drop the mailbox into a
            # full re-backfill. The cursor update stays inside the same UoW
            # commit below.
            if msg is None:
                if new_hid:
                    stats.last_history_id = new_hid
                continue
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
