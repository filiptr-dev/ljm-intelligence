"""Email API — thin router over ``app.outreach.email_service``.

Two routes:

  POST /email/draft  — compose a Gemini/Claude or template-fallback draft
  POST /email/send   — send the composed email (owner-only)

Business logic (prompt building, fallback template, mode resolution, SentLog)
all lives in the service.
"""

from __future__ import annotations

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
    if not payload.lead_id and not payload.lead:
        raise HTTPException(400, "provide lead_id or lead")
    try:
        result = await svc_draft(
            request.app.state.sessionmaker,
            settings,
            lead_id=payload.lead_id,
            lead_payload=payload.lead,
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
    to: EmailStr
    subject: str
    body: str
    body_html: str | None = None
    lead_id: str | None = None
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
    env) and persists SentLog with provider fields."""
    settings: Settings = request.app.state.settings
    result = await svc_send(
        request.app.state.sessionmaker,
        settings,
        to=str(payload.to),
        subject=payload.subject,
        body=payload.body,
        body_html=payload.body_html,
        lead_id=payload.lead_id,
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
