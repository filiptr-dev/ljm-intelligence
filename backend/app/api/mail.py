"""Mail connector routes — status, test send, backfill, incremental, disconnect.

Two routers, same ``/mail`` prefix:

* ``router`` — owner-only (``user_only``): /status, /test-send, /mailboxes,
  /disconnect. A cron secret must never flip the owner's mail mode or trigger
  a test send.
* ``cron_router`` — user-or-cron: /backfill + /incremental. These are the
  cron-driven ingest routes; the per-handler ``check_secret`` call stays as
  the authoritative guard.

FastAPI only applies one dependency stack per ``include_router`` call, so
splitting is the cheapest way to express "most of this is owner-only; two of
these also accept cron". The route paths are unchanged — no URL moves.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, Header, Request
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select

from app.api._auth import check_secret
from app.config import Settings
from app.mail.credentials import load_sa_info, sa_fingerprint
from app.mail.ingest import ingest_backfill, ingest_incremental
from app.mail.mailbox import get_mailbox_source
from app.mail.sender import get_mail_sender
from app.models import MailCursor, SentLog, SettingsRow

log = logging.getLogger(__name__)

router = APIRouter(prefix="/mail", tags=["mail"])
cron_router = APIRouter(prefix="/mail", tags=["mail"])


# ---------- shapes ----------------------------------------------------------


class MailStatusOut(BaseModel):
    mode: Literal["simulated", "gmail"]
    impersonate: str
    admin_impersonate: str
    scopes: list[str]
    sa_configured: bool
    sa_fingerprint: str | None
    postal_address_set: bool
    sends_today: int
    last_message_id: str | None
    reason: str | None = None


class TestSendIn(BaseModel):
    to: EmailStr


class TestSendOut(BaseModel):
    ok: bool
    mode: Literal["simulated", "real"]
    message_id: str | None
    thread_id: str | None
    reason: str | None = None


class BackfillIn(BaseModel):
    mailbox: EmailStr
    months: int = Field(default=12, ge=1, le=120)


class IncrementalIn(BaseModel):
    mailbox: EmailStr | None = None


class IngestStatsOut(BaseModel):
    mailbox: str
    read: int
    upserted: int
    skipped: int
    last_history_id: str | None
    status: str
    error: str | None = None


class IncrementalOut(BaseModel):
    items: list[IngestStatsOut]


class DisconnectOut(BaseModel):
    ok: bool
    mode: Literal["simulated"]


class MailboxOut(BaseModel):
    email: str
    last_history_id: str | None
    backfilled_through: datetime | None


class MailboxesOut(BaseModel):
    items: list[MailboxOut]


# ---------- helpers --------------------------------------------------------


async def _effective_mode(request: Request) -> str:
    """DB override wins over env (per plan: env > DB > default, but owner-flip
    needs to work live, so we invert for the override path: DB override wins)."""
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
        if row and row.mail_sender_override:
            return row.mail_sender_override
    return request.app.state.settings.mail_sender


# ---------- routes ---------------------------------------------------------


@router.get("/status", response_model=MailStatusOut)
async def status(request: Request) -> MailStatusOut:
    settings: Settings = request.app.state.settings
    mode = await _effective_mode(request)
    sa = load_sa_info(settings.gmail_sa_json.get_secret_value() if settings.gmail_sa_json else None)
    fingerprint = sa_fingerprint(sa) if sa else None
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        today = datetime.now(UTC).date()
        sends_today = (
            await s.execute(select(func.count(SentLog.id)).where(func.date(SentLog.sent_at) == today))
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
    return MailStatusOut(
        mode=mode if mode in ("simulated", "gmail") else "simulated",
        impersonate=settings.gmail_impersonate,
        admin_impersonate=settings.gmail_admin_impersonate,
        scopes=list(settings.gmail_scopes_send) + list(settings.gmail_scopes_read) + list(settings.gmail_scopes_admin),
        sa_configured=sa is not None,
        sa_fingerprint=fingerprint,
        postal_address_set=bool((settings.outreach_postal_address or "").strip()),
        sends_today=int(sends_today),
        last_message_id=last,
        reason=reason,
    )


@router.post("/test-send", response_model=TestSendOut)
async def test_send(payload: TestSendIn, request: Request) -> TestSendOut:
    settings: Settings = request.app.state.settings
    mode = await _effective_mode(request)
    sender = get_mail_sender(settings, mode_override=mode)
    result = await sender.send(
        to=str(payload.to),
        subject="LJM Intelligence — connection test",
        body="This is a test from LJM Intelligence /mail/test-send.",
        from_addr=settings.outreach_from_email,
    )
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        s.add(
            SentLog(
                lead_id="SYSTEM",
                mode=result.mode,
                to_email=str(payload.to),
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
    return TestSendOut(
        ok=result.error is None,
        mode=result.mode,
        message_id=result.message_id,
        thread_id=result.thread_id,
        reason=result.error,
    )


class _MailboxForm(BaseModel):
    mailbox: EmailStr


@cron_router.post("/backfill", response_model=IngestStatsOut)
async def backfill(
    payload: BackfillIn,
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> IngestStatsOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    source = get_mailbox_source(settings)
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        stats = await ingest_backfill(s, source, str(payload.mailbox), months=payload.months)
    return IngestStatsOut(**stats.__dict__)


@cron_router.post("/incremental", response_model=IncrementalOut)
async def incremental(
    payload: IncrementalIn,
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> IncrementalOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    source = get_mailbox_source(settings)
    mailboxes = [str(payload.mailbox)] if payload.mailbox else await source.list_mailboxes()
    items: list[IngestStatsOut] = []
    sessionmaker = request.app.state.sessionmaker
    for mbx in mailboxes:
        async with sessionmaker() as s:
            stats = await ingest_incremental(s, source, mbx)
            items.append(IngestStatsOut(**stats.__dict__))
    return IncrementalOut(items=items)


@router.post("/disconnect", response_model=DisconnectOut)
async def disconnect(request: Request) -> DisconnectOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
        if row is None:
            row = SettingsRow(id=1)
            s.add(row)
        row.mail_sender_override = "simulated"
        await s.commit()
    return DisconnectOut(ok=True, mode="simulated")


@router.get("/mailboxes", response_model=MailboxesOut)
async def mailboxes(request: Request) -> MailboxesOut:
    settings: Settings = request.app.state.settings
    source = get_mailbox_source(settings)
    emails = await source.list_mailboxes()
    sessionmaker = request.app.state.sessionmaker
    items: list[MailboxOut] = []
    async with sessionmaker() as s:
        for email in emails:
            cur = (await s.execute(select(MailCursor).where(MailCursor.mailbox == email))).scalar_one_or_none()
            items.append(
                MailboxOut(
                    email=email,
                    last_history_id=cur.history_id if cur else None,
                    backfilled_through=cur.backfilled_through_at if cur else None,
                )
            )
    return MailboxesOut(items=items)


__all__ = ["cron_router", "router"]
