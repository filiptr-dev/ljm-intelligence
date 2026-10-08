"""Mail connector service — status / test-send / mailbox listing / disconnect.

Thin business layer behind ``app/api/mail.py``. The adapter layer
(``app.integrations.adapters.email``) still owns Gmail I/O and the ingest
pipeline; this service hoists the DB-side reads/writes + mode resolution out
of the router so the cron-side ingest flows can share them.

Returns plain dataclasses. No FastAPI.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.inbox.models import MailCursor
from app.integrations.adapters.email.credentials import load_sa_info, sa_fingerprint
from app.integrations.adapters.email.ingest import ingest_backfill, ingest_incremental
from app.integrations.adapters.email.mailbox import get_mailbox_source
from app.integrations.adapters.email.sender import get_mail_sender
from app.outreach.models import SentLog

log = logging.getLogger(__name__)


@dataclass
class MailStatusRow:
    mode: str  # "simulated" | "gmail"
    impersonate: str
    admin_impersonate: str
    scopes: list[str]
    sa_configured: bool
    sa_fingerprint: str | None
    postal_address_set: bool
    sends_today: int
    last_message_id: str | None
    reason: str | None = None
    owner_send_enabled: bool = False
    mailbox_source: str = "simulated"
    read_mailboxes_count: int = 0


@dataclass
class TestSendRow:
    ok: bool
    mode: str  # "simulated" | "real"
    message_id: str | None
    thread_id: str | None
    reason: str | None = None


@dataclass
class IngestStatsRow:
    mailbox: str
    read: int
    upserted: int
    skipped: int
    last_history_id: str | None
    status: str
    error: str | None = None


@dataclass
class IncrementalResult:
    items: list[IngestStatsRow] = field(default_factory=list)


@dataclass
class MailboxRow:
    email: str
    last_history_id: str | None
    backfilled_through: datetime | None


async def effective_mode(session: AsyncSession, settings: Any) -> str:
    """DB override wins over env.

    Env is the default; a /settings flip (writing ``mail_sender_override``)
    takes over at runtime so the owner can switch without a redeploy.
    """
    row = (
        await session.execute(select(SettingsRow).where(SettingsRow.id == 1))
    ).scalar_one_or_none()
    if row and row.mail_sender_override:
        return row.mail_sender_override
    return settings.mail_sender


async def status(sessionmaker: Any, settings: Any) -> MailStatusRow:
    async with sessionmaker() as s:
        mode = await effective_mode(s, settings)
    sa = load_sa_info(
        settings.gmail.sa_json.get_secret_value() if settings.gmail.sa_json else None
    )
    fingerprint = sa_fingerprint(sa) if sa else None
    async with sessionmaker() as s:
        today = datetime.now(UTC).date()
        sends_today = (
            await s.execute(
                select(func.count(SentLog.id)).where(func.date(SentLog.sent_at) == today)
            )
        ).scalar() or 0
        last = (
            await s.execute(
                select(SentLog.provider_message_id)
                .where(SentLog.provider_message_id.is_not(None))
                .order_by(SentLog.sent_at.desc())
                .limit(1)
            )
        ).scalar()
    reason = None
    if mode == "gmail" and sa is None:
        reason = "missing_env:GMAIL_SA_JSON"
    elif mode == "gmail" and not getattr(settings, "mail_owner_send_enabled", False):
        reason = "owner_switch_off:MAIL_OWNER_SEND_ENABLED"
    # Read-side mailbox count — informational only; a real list call happens
    # on `/mail/mailboxes` so this stays cheap.
    read_mailboxes_count = 0
    if settings.mailbox_source == "gmail" and sa is not None:
        try:
            from app.integrations.adapters.email.mailbox import GmailMailbox
            read_mailboxes_count = len(await GmailMailbox(settings).list_mailboxes())
        except Exception as exc:  # noqa: BLE001
            log.warning("mail/status: list_mailboxes probe failed: %s", exc)
    return MailStatusRow(
        mode=mode if mode in ("simulated", "gmail") else "simulated",
        impersonate=settings.gmail.impersonate,
        admin_impersonate=settings.gmail.admin_impersonate,
        scopes=list(settings.gmail.scopes_send)
        + list(settings.gmail.scopes_read)
        + list(settings.gmail.scopes_admin),
        sa_configured=sa is not None,
        sa_fingerprint=fingerprint,
        postal_address_set=bool((settings.outreach_postal_address or "").strip()),
        sends_today=int(sends_today),
        last_message_id=last,
        reason=reason,
        owner_send_enabled=bool(getattr(settings, "mail_owner_send_enabled", False)),
        mailbox_source=settings.mailbox_source,
        read_mailboxes_count=read_mailboxes_count,
    )


async def test_send(sessionmaker: Any, settings: Any, *, to: str) -> TestSendRow:
    async with sessionmaker() as s:
        mode = await effective_mode(s, settings)
    sender = get_mail_sender(settings, mode_override=mode)
    result = await sender.send(
        to=to,
        subject="LJM Intelligence — connection test",
        body="This is a test from LJM Intelligence /mail/test-send.",
        from_addr=settings.outreach_from_email,
    )
    async with sessionmaker() as s:
        s.add(
            SentLog(
                lead_id="SYSTEM",
                mode=result.mode,
                to_email=to,
                subject="LJM Intelligence — connection test",
                body="(test)",
                provider_message_id=result.message_id,
                thread_id=result.thread_id,
                is_test=True,
            )
        )
        try:
            await s.commit()
        except Exception as exc:  # noqa: BLE001
            await s.rollback()
            log.warning("mail/test-send: sent_log insert skipped: %s", exc)
    return TestSendRow(
        ok=result.error is None,
        mode=result.mode,
        message_id=result.message_id,
        thread_id=result.thread_id,
        reason=result.error,
    )


async def backfill(
    sessionmaker: Any, settings: Any, *, mailbox: str, months: int
) -> IngestStatsRow:
    source = get_mailbox_source(settings)
    async with sessionmaker() as s:
        stats = await ingest_backfill(s, source, mailbox, months=months)
    return IngestStatsRow(**stats.__dict__)


async def incremental(
    sessionmaker: Any, settings: Any, *, mailbox: str | None
) -> IncrementalResult:
    source = get_mailbox_source(settings)
    mailboxes = [mailbox] if mailbox else await source.list_mailboxes()
    items: list[IngestStatsRow] = []
    for mbx in mailboxes:
        async with sessionmaker() as s:
            stats = await ingest_incremental(s, source, mbx)
            items.append(IngestStatsRow(**stats.__dict__))
    return IncrementalResult(items=items)


async def disconnect(sessionmaker: Any) -> None:
    async with sessionmaker() as s:
        row = (
            await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        if row is None:
            row = SettingsRow(id=1)
            s.add(row)
        row.mail_sender_override = "simulated"
        await s.commit()


async def reconnect(sessionmaker: Any) -> None:
    """Clear the DB-side simulated override so the env `mail_sender` wins again.

    Mirrors `disconnect`. The connector-setup findings called this out: once
    an owner clicked Disconnect, the override stuck forever with no UI to
    bring Gmail back. ``mail_sender_override = NULL`` means "defer to env".
    """
    async with sessionmaker() as s:
        row = (
            await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        if row is None:
            row = SettingsRow(id=1)
            s.add(row)
        row.mail_sender_override = None
        await s.commit()


@dataclass
class TestReadRow:
    """Result of /mail/test-read — the read-side companion to /mail/test-send."""

    ok: bool
    mode: str  # "simulated" | "gmail"
    mailboxes_found: int
    sample_subject: str | None = None
    sample_from: str | None = None
    reason: str | None = None


async def test_read(settings: Any) -> TestReadRow:
    """Prove the configured mailbox source can actually read mail.

    The old Mail connection panel claimed "Test connection switches to live
    Gmail", but under the hood it only sent a test email. That left a whole
    class of DWD misconfiguration (missing admin scope, impersonation
    subject wrong, read scope not granted) invisible until the first cron
    hit. This endpoint lists at most one mailbox, pulls its most recent
    message, returns the subject + from — no DB writes, nothing persisted.
    """
    source = get_mailbox_source(settings)
    try:
        mailboxes = await source.list_mailboxes()
    except Exception as exc:  # noqa: BLE001
        return TestReadRow(ok=False, mode=source.kind, mailboxes_found=0, reason=str(exc))
    if not mailboxes:
        return TestReadRow(
            ok=False, mode=source.kind, mailboxes_found=0,
            reason="no_mailboxes_listed (check admin_impersonate + admin scope)",
        )
    first = mailboxes[0]
    try:
        async for msg in source.backfill(first, datetime.now(UTC).replace(day=1)):
            return TestReadRow(
                ok=True, mode=source.kind, mailboxes_found=len(mailboxes),
                sample_subject=msg.subject, sample_from=msg.from_addr,
            )
    except Exception as exc:  # noqa: BLE001
        return TestReadRow(
            ok=False, mode=source.kind, mailboxes_found=len(mailboxes), reason=str(exc)
        )
    return TestReadRow(
        ok=True, mode=source.kind, mailboxes_found=len(mailboxes),
        sample_subject=None, sample_from=None,
        reason="listed_but_no_recent_messages",
    )


async def list_mailboxes(sessionmaker: Any, settings: Any) -> list[MailboxRow]:
    source = get_mailbox_source(settings)
    emails = await source.list_mailboxes()
    items: list[MailboxRow] = []
    async with sessionmaker() as s:
        for email in emails:
            cur = (
                await s.execute(select(MailCursor).where(MailCursor.mailbox == email))
            ).scalar_one_or_none()
            items.append(
                MailboxRow(
                    email=email,
                    last_history_id=cur.history_id if cur else None,
                    backfilled_through=cur.backfilled_through_at if cur else None,
                )
            )
    return items
