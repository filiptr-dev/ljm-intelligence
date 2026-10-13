"""Inbox router — HTTP surface for the inbox-analysis plan.

Thin FastAPI → ``app.inbox.service`` adapter. The router validates the query
shape, calls the service, and serialises dataclasses to JSON. No business
logic here. See the plan at
projects/ljm-intelligence/plan/2026-10-01-inbox-analysis-gmail-connector.md.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.db import Session
from app.inbox import service as svc
from app.integrations.adapters.email.credentials import resolve_mail_read_allowlist

router = APIRouter(prefix="/inbox", tags=["inbox"])


def _validate_mailbox(request: Request, mailbox: str | None) -> str | None:
    """Lowercase + membership-check against the mail-read allowlist.

    The allowlist (``settings.mail_read_allowlist`` → vault cache) is the
    one trust boundary for "which mailboxes the app may touch". Reusing it
    here keeps us from inventing a second rule that could drift.
    """
    if mailbox is None:
        return None
    normalized = mailbox.strip().lower()
    if not normalized:
        return None
    allow = resolve_mail_read_allowlist(request.app.state.settings)
    if allow is None or normalized not in allow:
        raise HTTPException(status_code=400, detail="mailbox not allowed")
    return normalized


class MailboxItem(BaseModel):
    address: str


class MailboxesListOut(BaseModel):
    items: list[MailboxItem]


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


@router.get("/mailboxes", response_model=MailboxesListOut)
async def list_mailboxes_endpoint(request: Request) -> MailboxesListOut:
    """List the mailboxes the operator may filter by.

    Derived from the mail-read allowlist — the single source of truth for
    "which addresses the app will touch" — so the selector can't offer a
    mailbox the backend would reject.
    """
    allow = resolve_mail_read_allowlist(request.app.state.settings) or set()
    items = [MailboxItem(address=addr) for addr in sorted(allow)]
    return MailboxesListOut(items=items)


@router.get("/emails", response_model=EmailsPageOut)
async def list_emails_endpoint(
    session: Session,
    request: Request,
    intent: str | None = Query(default=None, max_length=32),
    q: str | None = Query(default=None, max_length=200),
    page: int = Query(default=1, ge=1, le=10000),
    page_size: int = Query(default=40, ge=1, le=200),
    mailbox: str | None = Query(default=None, max_length=320),
) -> EmailsPageOut:
    """List emails for the /emails page. Returns the triage counts + sentiment
    bar numbers in one call so the Server Component doesn't need a second trip.
    """
    mb = _validate_mailbox(request, mailbox)
    offset = (page - 1) * page_size
    rows, total = await svc.list_emails(
        session, intent=intent, text_query=q, limit=page_size, offset=offset, mailbox=mb,
    )
    counts = await svc.intent_counts(session, mailbox=mb)
    bucket = await svc.sentiment_buckets(session, mailbox=mb)
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
    request: Request,
    urgent_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    mailbox: str | None = Query(default=None, max_length=320),
) -> list[TriageOut]:
    mb = _validate_mailbox(request, mailbox)
    items = await svc.triage_list(session, urgent_only=urgent_only, limit=limit, mailbox=mb)
    return [TriageOut(**i.__dict__) for i in items]


@router.get("/no-reply", response_model=list[NoReplyOut])
async def no_reply_endpoint(
    session: Session,
    request: Request,
    limit: int = Query(default=50, ge=1, le=200),
    mailbox: str | None = Query(default=None, max_length=320),
) -> list[NoReplyOut]:
    mb = _validate_mailbox(request, mailbox)
    items = await svc.no_reply_list(session, limit=limit, mailbox=mb)
    return [NoReplyOut(**i.__dict__) for i in items]


@router.get("/response-time", response_model=ResponseTimeOut)
async def response_time_endpoint(
    session: Session,
    broker_domain: str | None = Query(default=None, max_length=255),
) -> ResponseTimeOut:
    stats = await svc.response_time_stats(session, broker_domain=broker_domain)
    return ResponseTimeOut(**stats.__dict__)


@router.get("/staff", response_model=list[StaffOut])
async def staff_endpoint(
    session: Session,
    request: Request,
    mailbox: str | None = Query(default=None, max_length=320),
) -> list[StaffOut]:
    mb = _validate_mailbox(request, mailbox)
    items = await svc.staff_performance(session, mailbox=mb)
    return [StaffOut(**i.__dict__) for i in items]


@router.get("/overview-kpis", response_model=KPIsOut)
async def overview_kpis_endpoint(
    session: Session,
    request: Request,
    mailbox: str | None = Query(default=None, max_length=320),
) -> KPIsOut:
    mb = _validate_mailbox(request, mailbox)
    k = await svc.overview_kpis(session, mailbox=mb)
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


# ---- status board + compose/reply + forget-contact (plan Step 4/5/8) -----


from pydantic import EmailStr, constr


class StatusBoardRowOut(BaseModel):
    thread_id: str
    mailbox: str
    subject: str
    broker_name: str | None
    counterparty: str
    last_direction: str
    last_sent_at: datetime | None
    owner_user_id: str | None
    stage: str
    next_step: str
    message_count: int


class EmailDesignIn(BaseModel):
    """Design block the rich builder sends alongside the body.

    Field names mirror the frontend ``EmailDesign`` type one-for-one so
    the generated OpenAPI client types stay round-trippable.
    """

    accent_hex: str | None = None
    signature: bool = True
    logo: bool = True
    cta_label: constr(max_length=48) = ""
    cta_url: str = ""
    layout: constr(max_length=16) = "branded"
    show_truck: bool = True


class ReplyIn(BaseModel):
    body_text: constr(min_length=1, max_length=50_000)
    body_html: constr(min_length=0, max_length=200_000) = ""
    design: EmailDesignIn | None = None
    tone: constr(max_length=24) | None = None
    purpose: constr(max_length=32) | None = None


class ReplyOut(BaseModel):
    ok: bool
    mode: str | None = None
    message_id: str | None = None
    to: str | None = None
    reason: str | None = None


class ComposeIn(BaseModel):
    to: EmailStr
    subject: constr(min_length=1, max_length=255)
    body_text: constr(min_length=1, max_length=50_000)
    body_html: constr(min_length=0, max_length=200_000) = ""
    design: EmailDesignIn | None = None
    tone: constr(max_length=24) | None = None
    purpose: constr(max_length=32) | None = None


class ComposeOut(BaseModel):
    ok: bool
    mode: str | None = None
    message_id: str | None = None
    thread_id: str | None = None
    reason: str | None = None


class AiDraftOutModel(BaseModel):
    subject: str
    body_text: str
    body_html: str
    # True when a real AI provider produced the body; False when the
    # service fell back to the stock template. The UI uses this to show
    # an honest "AI couldn't write it" toast instead of silently handing
    # the operator a stock email that reads like the AI wrote it.
    ai_used: bool = False
    ai_error: str | None = None


class ForgetContactIn(BaseModel):
    email: EmailStr


class ForgetContactOut(BaseModel):
    ok: bool
    email: str | None = None
    messages_deleted: int = 0
    insights_deleted: int = 0
    reason: str | None = None


@router.get("/status-board", response_model=list[StatusBoardRowOut])
async def status_board_endpoint(
    session: Session,
    request: Request,
    limit: int = Query(default=200, ge=1, le=1000),
    mailbox: str | None = Query(default=None, max_length=320),
) -> list[StatusBoardRowOut]:
    mb = _validate_mailbox(request, mailbox)
    rows = await svc.status_board(session, limit=limit, mailbox=mb)
    return [StatusBoardRowOut(**r.__dict__) for r in rows]


@router.get("/threads/{thread_id}/ai-draft", response_model=AiDraftOutModel | None)
async def ai_draft_endpoint(
    session: Session, thread_id: str,
    tone: str = Query(default="professional", max_length=24),
    hint: str | None = Query(default=None, max_length=400),
):
    # ``hint`` is the broker-overview "next step" phrase — appended to the
    # prompt as the operator's instruction. Empty / absent hint returns the
    # same body today's clients see.
    draft = await svc.ai_draft_reply(session, thread_id, hint=hint or None)
    if draft is None:
        return None
    return AiDraftOutModel(**draft.__dict__)


@router.post("/threads/{thread_id}/reply", response_model=ReplyOut)
async def reply_endpoint(session: Session, thread_id: str, payload: ReplyIn, request: Request) -> ReplyOut:
    settings = request.app.state.settings
    design = payload.design.model_dump() if payload.design else None
    result = await svc.send_reply(
        session, thread_id=thread_id,
        body_text=payload.body_text, body_html=payload.body_html or payload.body_text,
        settings=settings, design=design,
    )
    await session.commit()
    return ReplyOut(**result)


@router.post("/compose", response_model=ComposeOut)
async def compose_endpoint(session: Session, payload: ComposeIn, request: Request) -> ComposeOut:
    settings = request.app.state.settings
    design = payload.design.model_dump() if payload.design else None
    result = await svc.send_new_email(
        session, to=str(payload.to), subject=payload.subject,
        body_text=payload.body_text, body_html=payload.body_html or payload.body_text,
        settings=settings, design=design,
    )
    await session.commit()
    return ComposeOut(**result)


# ---- AI draft (compose) + rewrite ----------------------------------------


class AiDraftComposeIn(BaseModel):
    to: EmailStr
    purpose: constr(max_length=32)
    tone: constr(max_length=24) = "professional"
    brief: constr(max_length=4_000) = ""
    recipient_name: constr(max_length=200) = ""
    lane: constr(max_length=120) = ""
    equipment: constr(max_length=120) = ""
    hint: constr(max_length=400) = ""


class RewriteIn(BaseModel):
    body_text: constr(min_length=1, max_length=50_000)
    tone: constr(max_length=24) = "professional"
    brief: constr(max_length=4_000) = ""


@router.post("/ai-draft", response_model=AiDraftOutModel)
async def ai_draft_compose_endpoint(
    payload: AiDraftComposeIn, session: Session,
) -> AiDraftOutModel:
    """Compose-time AI draft. Reuses the ``inbox_draft_reply`` AI feature."""
    draft = await svc.ai_draft_compose(
        to=str(payload.to), purpose=payload.purpose, tone=payload.tone,
        brief=payload.brief or None, recipient_name=payload.recipient_name or None,
        lane=payload.lane or None, equipment=payload.equipment or None,
        hint=payload.hint or None, session=session,
    )
    return AiDraftOutModel(**draft.__dict__)


class ChipFilterIn(BaseModel):
    """Structured recipe the chip miner used to count — replayed by Ask so the
    retrieved emails match the count the chip advertised. All fields optional;
    unknown keys / wrong types are dropped server-side (``_sanitise_chip_filter``).
    """

    broker_domain: str | None = None
    intent: str | None = None
    sentiment_below: float | None = None
    lane_from: str | None = None
    lane_to: str | None = None
    days: int | None = None
    pending_only: bool | None = None


class AskIn(BaseModel):
    question: constr(min_length=1, max_length=500)
    chip: ChipFilterIn | None = None


class AskCitationModel(BaseModel):
    thread_id: str
    subject: str
    snippet: str
    sent_at: str
    from_addr: str
    intent: str | None = None
    sentiment: float | None = None


class AskOutModel(BaseModel):
    intent: str | None = None
    keywords: list[str] = []
    sentiment: str | None = None
    summary: str = ""
    answer: str = ""
    citations: list[AskCitationModel] = []
    ok: bool = False
    error: str | None = None


class SuggestionItem(BaseModel):
    text: str
    kind: str
    # The structured recipe behind the chip's count. Replayed on click
    # (POST /inbox/ask with ``chip``) so the retrieved emails match the
    # count the chip shows — see ``service._sanitise_chip_filter``.
    chip_filter: ChipFilterIn | None = None


class SuggestionsOut(BaseModel):
    """Payload for the /inbox/ask/suggestions endpoint.

    ``generated_at`` is the server timestamp of the response (not the
    cache insert) — the UI uses it only for debugging; the in-process
    TTL cache is the actual freshness contract.
    """

    suggestions: list[SuggestionItem]
    generated_at: datetime


@router.get("/ask/suggestions", response_model=SuggestionsOut)
async def ask_suggestions_endpoint(
    session: Session,
    request: Request,
    mailbox: str | None = Query(default=None, max_length=320),
) -> SuggestionsOut:
    """Return 0-6 concrete AI-suggested chip questions for the Ask-your-inbox
    strip, grounded in the real mailbox (named brokers, lanes, dates, counts).

    Never 500s — the service wraps each fact miner, and an empty mailbox
    just yields ``{suggestions: []}`` so the UI can render nothing.
    """
    mb = _validate_mailbox(request, mailbox)
    items = await svc.suggest_questions(session, mailbox=mb)
    return SuggestionsOut(
        suggestions=[
            SuggestionItem(
                text=s.text,
                kind=s.kind,
                chip_filter=ChipFilterIn(**s.chip_filter) if s.chip_filter else None,
            )
            for s in items
        ],
        generated_at=datetime.now(UTC),
    )


@router.post("/ask", response_model=AskOutModel)
async def ask_endpoint(
    session: Session,
    request: Request,
    payload: AskIn,
    mailbox: str | None = Query(default=None, max_length=320),
) -> AskOutModel:
    """Answer the operator's natural-language question against the real
    inbox via Gemini. Reuses the ``inbox_draft_reply`` AI feature slot.
    Never 500s — provider resolve/timeout/parse errors come back as
    ``ok=false`` with ``error`` naming the reason and ``answer`` a
    human-readable message, so the UI can always say *why*."""
    mb = _validate_mailbox(request, mailbox)
    chip = payload.chip.model_dump(exclude_none=True) if payload.chip else None
    out = await svc.ask_question(
        session=session, question=payload.question, mailbox=mb, chip_filter=chip,
    )
    return AskOutModel(
        intent=out.intent, keywords=out.keywords,
        sentiment=out.sentiment, summary=out.summary,
        answer=out.answer,
        citations=[AskCitationModel(**c.__dict__) for c in out.citations],
        ok=out.ok, error=out.error,
    )


@router.post("/rewrite", response_model=AiDraftOutModel)
async def ai_rewrite_endpoint(payload: RewriteIn, session: Session) -> AiDraftOutModel:
    """Rewrite the body in the given tone. Falls back to the original body
    on any provider error — never leaves the user with an empty editor."""
    out = await svc.ai_rewrite(
        body_text=payload.body_text, tone=payload.tone, brief=payload.brief or None,
        session=session,
    )
    return AiDraftOutModel(**out.__dict__)


@router.post("/forget-contact", response_model=ForgetContactOut)
async def forget_contact_endpoint(session: Session, payload: ForgetContactIn) -> ForgetContactOut:
    result = await svc.forget_contact(session, email=str(payload.email))
    await session.commit()
    return ForgetContactOut(**result)


# ---- related threads for a counterpart (plan Task 2) -----------------------


class CounterpartOut(BaseModel):
    address: str
    domain: str | None


class RelatedThreadOut(BaseModel):
    thread_id: str
    latest_subject: str
    latest_received_at: datetime
    latest_intent: str | None
    message_count: int


class RelatedThreadsOut(BaseModel):
    counterpart: CounterpartOut
    items: list[RelatedThreadOut]


@router.get("/threads/{thread_id}/related", response_model=RelatedThreadsOut)
async def related_threads_endpoint(session: Session, thread_id: str) -> RelatedThreadsOut:
    result = await svc.list_counterpart_threads(session, thread_id)
    if result is None:
        # Thread exists but has no non-owner side (edge case); or no match.
        return RelatedThreadsOut(
            counterpart=CounterpartOut(address="", domain=None), items=[],
        )
    return RelatedThreadsOut(
        counterpart=CounterpartOut(
            address=result.counterpart.address,
            domain=result.counterpart.domain,
        ),
        items=[
            RelatedThreadOut(
                thread_id=r.thread_id,
                latest_subject=r.latest_subject,
                latest_received_at=r.latest_received_at,
                latest_intent=r.latest_intent,
                message_count=r.message_count,
            )
            for r in result.items
        ],
    )


# ---- needs-reply list (plan Part C) ---------------------------------------


class NeedsReplyRowOut(BaseModel):
    thread_id: str
    mailbox: str
    subject: str
    from_addr: str
    broker_name: str | None
    intent: str
    urgency: str
    sent_at: datetime


class NeedsReplyOut(BaseModel):
    items: list[NeedsReplyRowOut]


@router.get("/needs-reply", response_model=NeedsReplyOut)
async def needs_reply_endpoint(
    session: Session,
    request: Request,
    mailbox: str | None = Query(default=None, max_length=320),
    limit: int = Query(default=50, ge=1, le=200),
) -> NeedsReplyOut:
    """Threads whose newest message is inbound and that we owe a reply on.

    Routine-low and auto-reply / OOO threads are excluded in the service.
    """
    mb = _validate_mailbox(request, mailbox)
    rows = await svc.fetch_needs_reply(session, mailbox=mb, limit=limit)
    return NeedsReplyOut(items=[NeedsReplyRowOut(**r.__dict__) for r in rows])


# ---- gradual Gmail history import (plan Task 3) ----------------------------


class BackfillProgressRow(BaseModel):
    mailbox: str
    backfilled_through_at: datetime | None
    floor_at: datetime


@router.get("/backfill/progress", response_model=list[BackfillProgressRow])
async def backfill_progress_endpoint(session: Session, request: Request) -> list[BackfillProgressRow]:
    """Per-mailbox gradual-import progress (newest-reached pointer + floor)."""
    from datetime import UTC as _UTC, datetime as _dt, timedelta as _td

    from sqlalchemy import select as _select

    from app.inbox.models import MailCursor as _MC

    rows = (await session.execute(_select(_MC.mailbox, _MC.backfilled_through_at))).all()
    now = _dt.now(_UTC)
    floor = now - _td(days=365)
    return [
        BackfillProgressRow(
            mailbox=r[0],
            backfilled_through_at=r[1],
            floor_at=floor,
        )
        for r in rows
    ]


class BackfillStepOut(BaseModel):
    ok: bool
    mailbox: str
    job_id: int | None = None


@router.post("/backfill/step", response_model=BackfillStepOut)
async def backfill_step_endpoint(request: Request, mailbox: str = Query(..., max_length=320)) -> BackfillStepOut:
    """Manual kick for the gradual historical import step for one mailbox.

    Idempotent at the DB level: the step reads ``backfilled_through_at``
    inside its own transaction, so two concurrent kicks can't
    double-advance the pointer past the window they computed.
    """
    normalized = _validate_mailbox(request, mailbox)
    if normalized is None:
        raise HTTPException(status_code=400, detail="mailbox required")
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.queue_dispatch import maybe_dispatch

    job_id = await maybe_dispatch(
        request.app.state.sessionmaker,
        "inbox.mail_backfill_step",
        tenant_id=LJM_TENANT_ID,
        mailbox=normalized,
    )
    if job_id is None:
        # No queue (sqlite tests): run inline.
        from app.inbox.jobs import mail_backfill_step as _step
        await _step(LJM_TENANT_ID, normalized)
    return BackfillStepOut(ok=True, mailbox=normalized, job_id=job_id)


# ---- notifications (plan Task 1) -------------------------------------------


class NotificationItemOut(BaseModel):
    id: int
    kind: str
    title: str
    body: str | None
    thread_id: str | None
    message_id: str | None
    load_id: int | None
    created_at: datetime
    read_at: datetime | None


class NotificationsListOut(BaseModel):
    unread: int
    items: list[NotificationItemOut]


class NotificationsOkOut(BaseModel):
    ok: bool
    updated: int = 0


@router.get("/notifications", response_model=NotificationsListOut, tags=["notifications"])
async def notifications_list_endpoint(
    session: Session, limit: int = Query(default=50, ge=1, le=200),
) -> NotificationsListOut:
    from app.inbox import notifications_service as nsvc

    items_raw = await nsvc.list_items(session, limit=limit)
    unread = await nsvc.count_unread(session)
    return NotificationsListOut(
        unread=unread,
        items=[NotificationItemOut(**row) for row in items_raw],
    )


@router.post(
    "/notifications/{notification_id}/read",
    response_model=NotificationsOkOut,
    tags=["notifications"],
)
async def notification_mark_read_endpoint(
    session: Session, notification_id: int,
) -> NotificationsOkOut:
    from app.inbox import notifications_service as nsvc

    changed = await nsvc.mark_read(session, notification_id)
    await session.commit()
    return NotificationsOkOut(ok=True, updated=1 if changed else 0)


@router.post(
    "/notifications/read-all",
    response_model=NotificationsOkOut,
    tags=["notifications"],
)
async def notifications_mark_all_read_endpoint(session: Session) -> NotificationsOkOut:
    from app.inbox import notifications_service as nsvc

    updated = await nsvc.mark_all_read(session)
    await session.commit()
    return NotificationsOkOut(ok=True, updated=updated)
