"""Email API — thin router over ``app.outreach.email_service``.

Two routes:

  POST /email/draft  — compose a Gemini/Claude or template-fallback draft
  POST /email/send   — send the composed email (owner-only)

Business logic (prompt building, fallback template, mode resolution, SentLog)
all lives in the service.
"""

from __future__ import annotations

from datetime import datetime, timezone

import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, EmailStr, Field

from app.config import Settings
from app.outreach.email_service import (
    LeadNotFoundError,
    Tone,
    draft as svc_draft,
    send as svc_send,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["email"])

Stance = Literal["positive", "neutral", "cooling"]


class DraftIn(BaseModel):
    lead_id: str | None = None
    lead: dict | None = None
    contact_id: int | None = None
    tone: Tone = "professional"
    instructions: str | None = Field(default=None, max_length=1000)


class DraftOut(BaseModel):
    subject: str
    body: str
    body_html: str
    source: Literal["gemini", "claude", "fallback"]
    tone: Tone
    lead_id: str | None = None
    stance: Stance | None = None


@router.post("/draft", response_model=DraftOut)
async def draft_email(payload: DraftIn, request: Request) -> DraftOut:
    settings: Settings = request.app.state.settings
    provided = sum(1 for v in (payload.lead_id, payload.lead, payload.contact_id) if v)
    if provided == 0:
        raise HTTPException(400, "provide lead_id, lead, or contact_id")
    if provided > 1:
        raise HTTPException(422, "pass exactly one of lead_id, lead, contact_id")
    try:
        result = await svc_draft(
            request.app.state.sessionmaker,
            settings,
            lead_id=payload.lead_id,
            lead_payload=payload.lead,
            contact_id=payload.contact_id,
            tone=payload.tone,
            instructions=payload.instructions,
        )
    except LeadNotFoundError as exc:
        raise HTTPException(404, "lead not found") from exc
    # Narrow the service's open "source: str" into the response literal.
    src: Literal["gemini", "claude", "fallback"] = (
        result.source if result.source in ("gemini", "claude", "fallback") else "fallback"
    )
    return DraftOut(
        subject=result.subject,
        body=result.body,
        body_html=result.body_html,
        source=src,
        tone=result.tone,
        lead_id=result.lead_id,
        stance=result.stance,
    )


class SendIn(BaseModel):
    to: EmailStr | None = None
    subject: str
    body: str
    body_html: str | None = None
    lead_id: str | None = None
    contact_id: int | None = None
    in_reply_to: str | None = None
    references: list[str] | None = None
    thread_id: str | None = None


class SendOut(BaseModel):
    ok: bool
    mode: Literal["simulated", "real"]
    message_id: str | None
    thread_id: str | None
    reason: str | None = None


@router.post("/send", response_model=SendOut)
async def send_email(payload: SendIn, request: Request) -> SendOut:
    """Owner-only. The service resolves the mail sender (DB override wins over
    env) and persists SentLog with provider fields.

    Accepts either ``lead_id`` + ``to`` or ``contact_id`` (which resolves
    ``to`` + stamps SentLog with the contact FK). Passing both ``contact_id``
    and ``lead_id`` is 422 — the composer owns one recipient at a time.
    """
    settings: Settings = request.app.state.settings
    if payload.contact_id is not None and payload.lead_id is not None:
        raise HTTPException(422, "pass exactly one of lead_id, contact_id")
    to_addr: str | None = str(payload.to) if payload.to else None
    resolved_lead_id = payload.lead_id
    if payload.contact_id is not None:
        from sqlalchemy import select as _select

        from app.prospecting.models import LeadContact

        async with request.app.state.sessionmaker() as s:
            c = (
                await s.execute(_select(LeadContact).where(LeadContact.id == payload.contact_id))
            ).scalar_one_or_none()
            if c is None:
                raise HTTPException(404, "contact not found")
            if not c.email:
                raise HTTPException(422, "contact has no email on file")
            to_addr = c.email
            resolved_lead_id = c.lead_id
    if not to_addr:
        raise HTTPException(422, "provide `to` (or a contact_id with an email)")
    result = await svc_send(
        request.app.state.sessionmaker,
        settings,
        to=to_addr,
        subject=payload.subject,
        body=payload.body,
        body_html=payload.body_html,
        lead_id=resolved_lead_id,
        contact_id=payload.contact_id,
        in_reply_to=payload.in_reply_to,
        references=payload.references,
        thread_id=payload.thread_id,
    )
    return SendOut(
        ok=result.ok,
        mode=result.mode if result.mode in ("simulated", "real") else "simulated",
        message_id=result.message_id,
        thread_id=result.thread_id,
        reason=result.reason,
    )


# ---------------------------------------------------------------------------
# Campaigns — bulk send to a contact segment (freight managers in v1).
# ---------------------------------------------------------------------------


class CampaignIn(BaseModel):
    segment: Literal["freight_manager"] = "freight_manager"
    tone: Tone = "professional"
    limit: int = Field(50, ge=1, le=500)
    dry_run: bool = True


class CampaignRecipient(BaseModel):
    contact_id: int
    lead_id: str
    name: str | None = None
    email: str


class CampaignOut(BaseModel):
    recipients: list[CampaignRecipient]
    enqueued: int = 0
    dry_run: bool = True


@router.post("/campaigns", response_model=CampaignOut)
async def run_campaign(payload: CampaignIn, request: Request) -> CampaignOut:
    """Fan-out send to a contact segment. v1 only supports ``freight_manager``.

    Dry-run returns the recipient list and sends nothing. The non-dry-run
    path enqueues one procrastinate job per contact (reuses
    ``outreach.send_to_contact``) so the per-send rate limit holds.
    """
    sm = request.app.state.sessionmaker
    from app.prospecting.contacts_repository import SqlLeadContactRepo
    from app.prospecting.contacts_service import list_segment_freight_managers
    from app.shared.queue import dispatch

    async with sm() as s:
        repo = SqlLeadContactRepo(s)
        rows, _ = await list_segment_freight_managers(repo, cursor=None, limit=payload.limit)
    recipients = [
        CampaignRecipient(contact_id=c.id, lead_id=c.lead_id, name=c.name, email=c.email)
        for c in rows
        if c.email  # skip contacts without a published email (hard rule)
    ]
    enqueued = 0
    if not payload.dry_run:
        from procrastinate.exceptions import AlreadyEnqueued

        # One campaign per segment+tone per UTC day: a re-run can't double-queue.
        campaign_key = f"{payload.segment}:{payload.tone}:{datetime.now(timezone.utc):%Y%m%d}"
        for r in recipients:
            try:
                jid = await dispatch(
                    "outreach.send_to_contact",
                    queueing_lock=f"campaign:{campaign_key}:{r.contact_id}",
                    contact_id=r.contact_id,
                    tone=payload.tone,
                )
            except AlreadyEnqueued:
                continue  # counted as skipped
            if jid is not None:
                enqueued += 1
    return CampaignOut(recipients=recipients, enqueued=enqueued, dry_run=payload.dry_run)
