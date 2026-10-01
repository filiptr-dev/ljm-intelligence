"""Inbox router — HTTP surface for the inbox-analysis plan.

Thin FastAPI → ``app.inbox.service`` adapter. The router validates the query
shape, calls the service, and serialises dataclasses to JSON. No business
logic here. See the plan at
projects/ljm-intelligence/plan/2026-10-01-inbox-analysis-gmail-connector.md.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.db import Session
from app.inbox import service as svc

router = APIRouter(prefix="/inbox", tags=["inbox"])


# ---- response shapes ------------------------------------------------------


class EmailListItem(BaseModel):
    message_id: str
    mailbox: str
    thread_id: str
    direction: str
    from_addr: str
    to_addr: str
    subject: str
    body_text: str
    sent_at: datetime
    broker_name: str | None
    intent: str
    urgency: str
    sentiment: float
    confidence: float
    rate_usd: float | None
    lane_from: str | None
    lane_to: str | None
    evidence: str | None


class IntentCountOut(BaseModel):
    intent: str
    count: int


class SentimentOut(BaseModel):
    positive: int
    neutral: int
    negative: int
    inbound_total: int


class EmailsPageOut(BaseModel):
    items: list[EmailListItem]
    total: int
    page: int
    page_size: int
    intent_counts: list[IntentCountOut]
    sentiment: SentimentOut


class TriageOut(BaseModel):
    thread_id: str
    message_id: str
    mailbox: str
    subject: str
    from_addr: str
    broker_name: str | None
    intent: str
    urgency: str
    sentiment: float
    sent_at: datetime
    waiting_minutes: int
    snippet: str


class NoReplyOut(BaseModel):
    thread_id: str
    mailbox: str
    to_email: str
    subject: str
    we_sent_at: datetime
    days_waiting: int
    suggested_nudge: str


class ResponseTimeOut(BaseModel):
    ours_median_minutes: int | None
    theirs_median_minutes: int | None
    ours_count: int
    theirs_count: int


class StaffOut(BaseModel):
    mailbox: str
    inbound: int
    outbound: int
    reply_speed_minutes: int | None
    dropped_threads: int


class KPIsOut(BaseModel):
    volume_7d: int
    open_threads: int
    urgent: int
    negative: int


class RelationshipOut(BaseModel):
    broker_domain: str
    broker_name: str | None
    health_score: int
    inbound: int
    outbound: int
    avg_sentiment: float
    last_contact_at: datetime | None
    complaints: int
    praise: int


class ThreadOut(BaseModel):
    thread_id: str
    subject: str
    messages: list[EmailListItem]


# ---- endpoints ------------------------------------------------------------


@router.get("/emails", response_model=EmailsPageOut)
async def list_emails_endpoint(
    session: Session,
    intent: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=40, ge=1, le=200),
) -> EmailsPageOut:
    """List emails for the /emails page. Returns the triage counts + sentiment
    bar numbers in one call so the Server Component doesn't need a second trip.
    """
    offset = (page - 1) * page_size
    rows, total = await svc.list_emails(
        session, intent=intent, text_query=q, limit=page_size, offset=offset
    )
    counts = await svc.intent_counts(session)
    bucket = await svc.sentiment_buckets(session)
    return EmailsPageOut(
        items=[EmailListItem(**r.__dict__) for r in rows],
        total=total,
        page=page,
        page_size=page_size,
        intent_counts=[IntentCountOut(intent=c.intent, count=c.count) for c in counts],
        sentiment=SentimentOut(
            positive=bucket.positive, neutral=bucket.neutral,
            negative=bucket.negative, inbound_total=bucket.inbound_total,
        ),
    )


@router.get("/triage", response_model=list[TriageOut])
async def triage_endpoint(
    session: Session,
    urgent_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
) -> list[TriageOut]:
    items = await svc.triage_list(session, urgent_only=urgent_only, limit=limit)
    return [TriageOut(**i.__dict__) for i in items]


@router.get("/no-reply", response_model=list[NoReplyOut])
async def no_reply_endpoint(
    session: Session, limit: int = Query(default=50, ge=1, le=200)
) -> list[NoReplyOut]:
    items = await svc.no_reply_list(session, limit=limit)
    return [NoReplyOut(**i.__dict__) for i in items]


@router.get("/response-time", response_model=ResponseTimeOut)
async def response_time_endpoint(
    session: Session,
    broker_domain: str | None = Query(default=None, max_length=255),
) -> ResponseTimeOut:
    stats = await svc.response_time_stats(session, broker_domain=broker_domain)
    return ResponseTimeOut(**stats.__dict__)


@router.get("/staff", response_model=list[StaffOut])
async def staff_endpoint(session: Session) -> list[StaffOut]:
    items = await svc.staff_performance(session)
    return [StaffOut(**i.__dict__) for i in items]


@router.get("/overview-kpis", response_model=KPIsOut)
async def overview_kpis_endpoint(session: Session) -> KPIsOut:
    k = await svc.overview_kpis(session)
    return KPIsOut(**k.__dict__)


@router.get("/relationship/{broker_domain}", response_model=RelationshipOut)
async def relationship_endpoint(session: Session, broker_domain: str) -> RelationshipOut:
    if not broker_domain or len(broker_domain) > 255:
        raise HTTPException(status_code=400, detail="invalid broker_domain")
    h = await svc.relationship_health(session, broker_domain=broker_domain.lower())
    return RelationshipOut(**h.__dict__)


@router.get("/threads/{thread_id}", response_model=ThreadOut)
async def thread_endpoint(session: Session, thread_id: str) -> ThreadOut:
    msgs = await svc.get_thread(session, thread_id)
    if not msgs:
        raise HTTPException(status_code=404, detail="thread not found")
    return ThreadOut(
        thread_id=thread_id,
        subject=msgs[-1].subject,
        messages=[EmailListItem(**m.__dict__) for m in msgs],
    )
