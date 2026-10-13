"""Notifications — bell feed writes + reads.

Called at the tail of :func:`app.integrations.adapters.email.ingest._upsert`
after triage, wrapped in the same ``try/except Exception: log.exception``
pattern used by ``_apply_load_extract``. A bug here must never prevent
mail from landing.

Backfill guard: ``create_from_message`` returns early if the message's
``received_at`` is older than 24h. This single gate works for both the
``mail_backfill`` CLI path and the gradual ``mail_backfill_step`` job
without threading a backfill flag through every call site.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.models import MailMessage, Notification
from app.integrations.adapters.email.mailbox import RawMessage
from app.prospecting.models import Load

log = logging.getLogger(__name__)

# Historical mail must not spam the bell. 24h is simpler to reason about
# than threading a backfill flag through five call sites — if the live
# incremental ever falls behind, the owner still sees the catch-up.
_BACKFILL_GUARD_HOURS = 24


async def create_from_message(
    session: AsyncSession,
    msg: RawMessage,
    *,
    intent: str,
    urgency: str,
) -> None:
    """Decide notification kind for ``msg`` and insert zero or one row.

    Never raises — the caller already wraps us, but we also guard here so
    a mis-shaped row cannot bubble out as an exception during an ingest
    the user is waiting on.
    """
    try:
        received_at = msg.received_at
        if received_at is None:
            return
        if received_at.tzinfo is None:
            received_at = received_at.replace(tzinfo=UTC)
        if received_at < datetime.now(UTC) - timedelta(hours=_BACKFILL_GUARD_HOURS):
            # Historical mail: suppress bell entirely.
            return

        kind: str | None = None
        load_id: int | None = None
        title = (msg.subject or "").strip() or "(no subject)"
        from_addr = (msg.from_addr or "").strip()

        if urgency == "urgent":
            kind = "urgent_email"
        elif intent == "load_offer":
            kind = "load_offer"
            # Best-effort join: inbox-sourced loads carry source_ref="msg:<id>".
            res = await session.execute(
                select(Load.id)
                .where(Load.source == "inbox")
                .where(Load.source_ref == f"msg:{msg.message_id}")
                .limit(1)
            )
            row = res.first()
            if row is not None:
                load_id = int(row[0])
        else:
            # Inbound reply on a thread we previously sent on.
            if msg.in_reply_to:
                res = await session.execute(
                    select(func.count())
                    .select_from(MailMessage)
                    .where(MailMessage.thread_id == msg.thread_id)
                    .where(MailMessage.from_addr == MailMessage.mailbox)
                )
                prior_outbound = int(res.scalar() or 0)
                if prior_outbound >= 1:
                    kind = "broker_reply"

        if kind is None:
            return

        session.add(
            Notification(
                kind=kind,
                title=_title_for(kind, title, from_addr),
                body=None,
                thread_id=msg.thread_id,
                message_id=msg.message_id,
                load_id=load_id,
            )
        )
    except Exception:  # pragma: no cover — defensive, caller also wraps
        log.exception(
            "notifications: create_from_message failed for mailbox=%r message_id=%r",
            getattr(msg, "mailbox", None),
            getattr(msg, "message_id", None),
        )


def _title_for(kind: str, subject: str, from_addr: str) -> str:
    sender = from_addr or "unknown sender"
    if kind == "broker_reply":
        return f"New reply from {sender}"
    if kind == "load_offer":
        return f"New load offer — {subject}"
    if kind == "urgent_email":
        return f"Urgent — {subject}"
    return subject


async def list_items(session: AsyncSession, *, limit: int = 50) -> list[dict[str, Any]]:
    res = await session.execute(
        select(Notification)
        .order_by(Notification.created_at.desc(), Notification.id.desc())
        .limit(limit)
    )
    items: list[dict[str, Any]] = []
    for n in res.scalars().all():
        items.append(
            {
                "id": int(n.id),
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "thread_id": n.thread_id,
                "message_id": n.message_id,
                "load_id": int(n.load_id) if n.load_id is not None else None,
                "created_at": n.created_at,
                "read_at": n.read_at,
            }
        )
    return items


async def count_unread(session: AsyncSession) -> int:
    res = await session.execute(
        select(func.count())
        .select_from(Notification)
        .where(Notification.read_at.is_(None))
    )
    return int(res.scalar() or 0)


async def mark_read(session: AsyncSession, notification_id: int) -> bool:
    now = datetime.now(UTC)
    res = await session.execute(
        update(Notification)
        .where(Notification.id == notification_id)
        .where(Notification.read_at.is_(None))
        .values(read_at=now)
    )
    return (res.rowcount or 0) > 0


async def mark_all_read(session: AsyncSession) -> int:
    now = datetime.now(UTC)
    res = await session.execute(
        update(Notification)
        .where(Notification.read_at.is_(None))
        .values(read_at=now)
    )
    return int(res.rowcount or 0)


__all__ = [
    "count_unread",
    "create_from_message",
    "list_items",
    "mark_all_read",
    "mark_read",
]
