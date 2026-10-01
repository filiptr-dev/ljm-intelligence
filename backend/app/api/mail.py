"""Mail connector routes — thin router over ``app.integrations.mail_service``.

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
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, BackgroundTasks, Header, Request
from pydantic import BaseModel, EmailStr, Field

from app.api._auth import check_secret
from app.config import Settings
from app.integrations.mail_service import (
    backfill as svc_backfill,
    disconnect as svc_disconnect,
    incremental as svc_incremental,
    list_mailboxes as svc_list_mailboxes,
    reconnect as svc_reconnect,
    status as svc_status,
    test_read as svc_test_read,
    test_send as svc_test_send,
)

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
    owner_send_enabled: bool = False
    mailbox_source: Literal["simulated", "gmail"] = "simulated"
    read_mailboxes_count: int = 0


class TestReadOut(BaseModel):
    ok: bool
    mode: Literal["simulated", "gmail"]
    mailboxes_found: int
    sample_subject: str | None = None
    sample_from: str | None = None
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
    # Set only on the queued path — the frontend polls `/jobs/{id}` until
    # the worker finishes, then refetches the mailbox view. On the inline
    # path (sqlite + tests) this stays null and the sync fields hold the
    # real result.
    job_id: int | None = None


class IncrementalOut(BaseModel):
    items: list[IngestStatsOut]
    job_id: int | None = None


class DisconnectOut(BaseModel):
    ok: bool
    mode: Literal["simulated"]


class MailboxOut(BaseModel):
    email: str
    last_history_id: str | None
    backfilled_through: datetime | None


class MailboxesOut(BaseModel):
    items: list[MailboxOut]


# ---------- routes ---------------------------------------------------------


@router.get("/status", response_model=MailStatusOut)
async def status(request: Request) -> MailStatusOut:
    settings: Settings = request.app.state.settings
    row = await svc_status(request.app.state.sessionmaker, settings)
    return MailStatusOut(**row.__dict__)


@router.post("/test-send", response_model=TestSendOut)
async def test_send(payload: TestSendIn, request: Request) -> TestSendOut:
    settings: Settings = request.app.state.settings
    row = await svc_test_send(request.app.state.sessionmaker, settings, to=str(payload.to))
    return TestSendOut(**row.__dict__)


@cron_router.post("/backfill", response_model=IngestStatsOut)
async def backfill(
    payload: BackfillIn,
    request: Request,
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> IngestStatsOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)

    # Whole-mailbox backfill would time out on Render free (web dynos die
    # mid-request on reclaim). Hand it to the worker when the queue exists;
    # fall back to inline ingest on sqlite/tests. The worker runs one
    # bounded slice per invocation — the ingest service's Gmail historyId
    # cursor makes the job resumable; the 5-min cron drain picks it up
    # again until the mailbox is fully backfilled.
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        request.app.state.sessionmaker,
        "inbox.mail_backfill",
        tenant_id=LJM_TENANT_ID,
        mailbox=str(payload.mailbox),
        months=payload.months,
    )
    if job_id is not None:
        from app.api.jobs import kick_in_process_drain
        background.add_task(
            kick_in_process_drain, request.app.state.sessionmaker, settings,
            seconds=settings.jobs_in_process_kick_seconds,
        )
        return IngestStatsOut(
            mailbox=str(payload.mailbox), read=0, upserted=0, skipped=0,
            last_history_id=None, status="queued", job_id=job_id,
        )

    row = await svc_backfill(
        request.app.state.sessionmaker,
        settings,
        mailbox=str(payload.mailbox),
        months=payload.months,
    )
    return IngestStatsOut(**row.__dict__)


@cron_router.post("/incremental", response_model=IncrementalOut)
async def incremental(
    payload: IncrementalIn,
    request: Request,
    background: BackgroundTasks,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> IncrementalOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)

    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        request.app.state.sessionmaker,
        "inbox.mail_incremental",
        tenant_id=LJM_TENANT_ID,
        mailbox=str(payload.mailbox) if payload.mailbox else None,
    )
    if job_id is not None:
        from app.api.jobs import kick_in_process_drain
        background.add_task(
            kick_in_process_drain, request.app.state.sessionmaker, settings,
            seconds=settings.jobs_in_process_kick_seconds,
        )
        return IncrementalOut(items=[], job_id=job_id)

    result = await svc_incremental(
        request.app.state.sessionmaker,
        settings,
        mailbox=str(payload.mailbox) if payload.mailbox else None,
    )
    return IncrementalOut(items=[IngestStatsOut(**r.__dict__) for r in result.items])


@router.post("/disconnect", response_model=DisconnectOut)
async def disconnect(request: Request) -> DisconnectOut:
    await svc_disconnect(request.app.state.sessionmaker)
    return DisconnectOut(ok=True, mode="simulated")


class ReconnectOut(BaseModel):
    ok: bool


@router.post("/reconnect", response_model=ReconnectOut)
async def reconnect(request: Request) -> ReconnectOut:
    """Clear the DB-side simulated override — defer to env `mail_sender` again.

    The pre-build findings called this out: `/disconnect` wrote
    `mail_sender_override='simulated'` with no way to clear it from the UI.
    `/reconnect` nulls that override; env settings take over from the next
    `/mail/status` read.
    """
    await svc_reconnect(request.app.state.sessionmaker)
    return ReconnectOut(ok=True)


@router.post("/test-read", response_model=TestReadOut)
async def test_read(request: Request) -> TestReadOut:
    """Read-side connection probe — proves the DWD + read scope actually works.

    Lists one mailbox, pulls its most recent message (no DB write) so an
    owner can tell the difference between "SA valid, send scope only" and
    "SA valid, DWD + admin + read scope all granted".
    """
    settings: Settings = request.app.state.settings
    row = await svc_test_read(settings)
    return TestReadOut(**row.__dict__)


@router.get("/mailboxes", response_model=MailboxesOut)
async def mailboxes(request: Request) -> MailboxesOut:
    settings: Settings = request.app.state.settings
    rows = await svc_list_mailboxes(request.app.state.sessionmaker, settings)
    return MailboxesOut(items=[MailboxOut(**r.__dict__) for r in rows])


__all__ = ["cron_router", "router"]
