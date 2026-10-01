"""Brokers API — real-data list + detail + activity timeline.

Three GET routes, all owner-only (wired at ``main.py``):

  GET  /brokers                — ranked list with contact summary + next-action chip.
  GET  /brokers/{id}           — full contact block + computed next action + timeline.
  GET  /brokers/{id}/activity  — paged timeline tail for the detail page.

The next-action rule table lives in ``app.pipeline.broker_next_action`` — a
pure function the caller feeds with pre-loaded inputs. See the plan
``projects/ljm-intelligence/plan/2026-10-01-brokers-real-data-next-action.md``.

Design notes
------------
* Deterministic Python-side sort: ``(next_action priority, -fit_score, name)``
  so an operator who refreshes sees the same order. Page size is small (≤50)
  so sorting in Python after the fetch is cheaper than clever SQL.
* No AI in the read path. The chip is a deterministic rule, not a model.
* Missing contact fields render as ``null`` → UI shows ``—``. Never fake
  anything.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import and_, desc, func, or_, select

from app.models import (
    CallOutcome,
    Lead,
    LeadContact,
    LeadContactProvenance,
    SentLog,
    Suppression,
)
from app.pipeline.broker_next_action import (
    PRIORITY,
    NextAction,
    NextActionInput,
    compute,
)

router = APIRouter(prefix="/brokers", tags=["brokers"])


# ---------- response schemas -----------------------------------------------


class ContactFieldOut(BaseModel):
    value: str | None = None
    source: str | None = None
    verified_at: str | None = None
    confidence: str | None = None


class NextActionOut(BaseModel):
    kind: Literal["call", "email", "follow_up", "wait"]
    reason: str
    due_at: str | None = None


class NamedContactOut(BaseModel):
    id: int
    name: ContactFieldOut
    title: ContactFieldOut
    email: ContactFieldOut
    phone: ContactFieldOut
    is_decision_maker: bool
    pipeline_status: str
    sighted_count: int
    linkedin_url: str | None = None


class BrokerRowOut(BaseModel):
    id: str
    name: str
    mc: str | None = None
    dot: str | None = None
    state: str
    city: str | None = None
    phone: ContactFieldOut
    primary_email: ContactFieldOut
    fit_score: int | None = None
    next_action: NextActionOut
    last_activity_at: str | None = None


class BrokerListOut(BaseModel):
    items: list[BrokerRowOut]
    next_cursor: str | None = None
    total: int


class ActivityCallOut(BaseModel):
    kind: Literal["call_outcome"] = "call_outcome"
    outcome: str
    logged_at: str
    note: str | None = None


class ActivityEmailOut(BaseModel):
    kind: Literal["email_sent"] = "email_sent"
    subject: str | None = None
    to_email: str
    sent_at: str
    replied_at: str | None = None
    mode: str


class ActivityPageOut(BaseModel):
    items: list[ActivityCallOut | ActivityEmailOut]
    next_cursor: str | None = None


class LastCallOut(BaseModel):
    outcome: str
    logged_at: str


class LastEmailOut(BaseModel):
    subject: str | None = None
    sent_at: str
    replied_at: str | None = None


class BrokerSummaryOut(BaseModel):
    sent_count_30d: int
    reply_count_30d: int
    last_call: LastCallOut | None = None
    last_email: LastEmailOut | None = None


class BrokerDetailBody(BrokerRowOut):
    address: ContactFieldOut
    linkedin_company_url: str | None = None
    website_url: str | None = None
    contacts: list[NamedContactOut]


class BrokerDetailOut(BaseModel):
    broker: BrokerDetailBody
    activity: list[ActivityCallOut | ActivityEmailOut]
    summary: BrokerSummaryOut


# ---------- helpers --------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return dt.isoformat()


def _field(
    value: str | None, source: str | None = None, verified_at: datetime | None = None, confidence: str | None = None
) -> ContactFieldOut:
    return ContactFieldOut(
        value=value or None,
        source=source,
        verified_at=_iso(verified_at),
        confidence=confidence,
    )


def _encode_cursor(ident: str) -> str:
    return base64.urlsafe_b64encode(ident.encode()).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


@dataclass
class _ContactAgg:
    email_count: int = 0
    phone_count: int = 0
    sendable_email_count: int = 0  # non-bounced, non-suppressed emails
    has_sendable_email: bool = False


# ---------- shared load for list + detail ----------------------------------


async def _load_next_action_inputs(
    session, lead_ids: list[str], today: date
) -> tuple[
    dict[str, CallOutcome | None],  # latest call per lead
    dict[str, SentLog | None],  # latest sent per lead
    dict[str, CallOutcome],  # pending callback (callback_at <= today) per lead
    dict[str, _ContactAgg],  # contact aggregates per lead
    dict[str, int],  # total call outcomes ever per lead
    dict[str, int],  # total sent_log ever per lead
]:
    if not lead_ids:
        return {}, {}, {}, {}, {}, {}

    # Latest call per lead — one row per lead using (lead_id, logged_at DESC).
    # Portable approach: pull outcomes for the lead-set and reduce in Python.
    call_rows = (await session.execute(select(CallOutcome).where(CallOutcome.lead_id.in_(lead_ids)))).scalars().all()
    latest_call: dict[str, CallOutcome] = {}
    pending_cb: dict[str, CallOutcome] = {}
    totals_call: dict[str, int] = {}
    for row in call_rows:
        totals_call[row.lead_id] = totals_call.get(row.lead_id, 0) + 1
        cur = latest_call.get(row.lead_id)
        if cur is None or row.logged_at > cur.logged_at:
            latest_call[row.lead_id] = row
        if row.outcome == "callback" and row.callback_at and row.callback_at <= today:
            # Keep the earliest-set callback that is now due (so "N days ago" reflects first setter).
            existing = pending_cb.get(row.lead_id)
            if existing is None or row.logged_at < existing.logged_at:
                pending_cb[row.lead_id] = row

    sent_rows = (await session.execute(select(SentLog).where(SentLog.lead_id.in_(lead_ids)))).scalars().all()
    latest_sent: dict[str, SentLog] = {}
    totals_sent: dict[str, int] = {}
    for row in sent_rows:
        totals_sent[row.lead_id] = totals_sent.get(row.lead_id, 0) + 1
        cur = latest_sent.get(row.lead_id)
        if cur is None or row.sent_at > cur.sent_at:
            latest_sent[row.lead_id] = row

    # Contact aggregates — need to know "has a sendable email" (non-bounced, non-suppressed).
    suppressed_emails = set((await session.execute(select(Suppression.email))).scalars().all())

    contact_rows = (await session.execute(select(LeadContact).where(LeadContact.lead_id.in_(lead_ids)))).scalars().all()
    agg: dict[str, _ContactAgg] = {lid: _ContactAgg() for lid in lead_ids}
    for c in contact_rows:
        a = agg.setdefault(c.lead_id, _ContactAgg())
        if c.email:
            a.email_count += 1
            if c.pipeline_status != "bounced" and c.email not in suppressed_emails:
                a.sendable_email_count += 1
                a.has_sendable_email = True
        if c.phone:
            a.phone_count += 1

    return latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent


def _compute_action_for(
    lead: Lead,
    latest_call: CallOutcome | None,
    latest_sent: SentLog | None,
    pending_cb: CallOutcome | None,
    agg: _ContactAgg,
    totals_call: int,
    totals_sent: int,
    now: datetime,
    today: date,
) -> NextAction:
    pending_days: int | None = None
    if pending_cb is not None:
        delta = today - (pending_cb.logged_at.date() if pending_cb.logged_at else today)
        pending_days = max(0, delta.days)
    inp = NextActionInput(
        latest_call_outcome=latest_call.outcome if latest_call else None,
        latest_call_logged_at=latest_call.logged_at if latest_call else None,
        pending_callback_today=pending_cb is not None,
        pending_callback_days_ago=pending_days,
        pending_callback_due=pending_cb.callback_at if pending_cb else None,
        latest_sent_at=latest_sent.sent_at if latest_sent else None,
        latest_sent_replied_at=latest_sent.replied_at if latest_sent else None,
        has_phone=bool(lead.phone),
        has_sendable_email=agg.has_sendable_email,
        total_call_outcomes=totals_call,
        total_sent=totals_sent,
    )
    return compute(inp, now)


def _row_out(
    lead: Lead,
    action: NextAction,
    last_activity_at: datetime | None,
) -> BrokerRowOut:
    return BrokerRowOut(
        id=lead.id,
        name=lead.name,
        mc=lead.mc,
        dot=lead.dot,
        state=lead.state,
        city=lead.city,
        phone=_field(lead.phone, lead.phone_source),
        primary_email=_field(lead.primary_email, lead.primary_email_source),
        fit_score=lead.fit_score,
        next_action=NextActionOut(
            kind=action.kind,
            reason=action.reason,
            due_at=action.due_at.isoformat() if action.due_at else None,
        ),
        last_activity_at=_iso(last_activity_at),
    )


# ---------- GET /brokers ---------------------------------------------------


@router.get("", response_model=BrokerListOut)
async def list_brokers(
    request: Request,
    state: str | None = Query(default=None, min_length=2, max_length=2),
    min_fit: int | None = Query(default=None, ge=0, le=100),
    has_email: bool | None = Query(default=None),
    has_phone: bool | None = Query(default=None),
    next_action: Literal["call", "email", "follow_up", "wait"] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> BrokerListOut:
    sessionmaker = request.app.state.sessionmaker
    today = _now().date()

    # Build the filter. We fetch all matching broker leads (fit + name filters)
    # then page in Python after sorting — the page size is small and the sort
    # key depends on next_action which is computed per-row.
    conds = [Lead.kind == "Broker"]
    if state:
        conds.append(Lead.state == state.upper())
    if min_fit is not None:
        conds.append(Lead.fit_score >= min_fit)
    if has_email is True:
        conds.append(Lead.primary_email.isnot(None))
    elif has_email is False:
        conds.append(Lead.primary_email.is_(None))
    if has_phone is True:
        conds.append(Lead.phone.isnot(None))
    elif has_phone is False:
        conds.append(Lead.phone.is_(None))
    if q:
        pat = f"%{q.strip()}%"
        conds.append(or_(Lead.name.ilike(pat), Lead.mc.ilike(pat), Lead.dot.ilike(pat)))

    async with sessionmaker() as s:
        leads = (await s.execute(select(Lead).where(and_(*conds)))).scalars().all()

        lead_ids = [l.id for l in leads]
        latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent = await _load_next_action_inputs(
            s, lead_ids, today
        )

    now = _now()
    enriched: list[tuple[int, int, str, BrokerRowOut, str]] = []
    for lead in leads:
        a = agg.get(lead.id, _ContactAgg())
        lc = latest_call.get(lead.id)
        ls = latest_sent.get(lead.id)
        pcb = pending_cb.get(lead.id)
        action = _compute_action_for(
            lead,
            lc,
            ls,
            pcb,
            a,
            totals_call.get(lead.id, 0),
            totals_sent.get(lead.id, 0),
            now,
            today,
        )
        if next_action and action.kind != next_action:
            continue
        last_activity = None
        for cand in (lc.logged_at if lc else None, ls.sent_at if ls else None):
            if cand is None:
                continue
            if last_activity is None or cand > last_activity:
                last_activity = cand
        row = _row_out(lead, action, last_activity)
        enriched.append(
            (
                PRIORITY[action.kind],
                -(lead.fit_score if lead.fit_score is not None else -1),
                (lead.name or "").lower(),
                row,
                lead.id,
            )
        )

    enriched.sort(key=lambda t: (t[0], t[1], t[2], t[4]))
    total = len(enriched)

    # Cursor is the lead id to start AFTER.
    start = 0
    if cursor:
        anchor = _decode_cursor(cursor)
        for i, item in enumerate(enriched):
            if item[4] == anchor:
                start = i + 1
                break

    page = enriched[start : start + limit]
    items = [item[3] for item in page]
    next_cursor = None
    if start + limit < len(enriched):
        next_cursor = _encode_cursor(page[-1][4])

    return BrokerListOut(items=items, next_cursor=next_cursor, total=total)


# ---------- GET /brokers/{id} ---------------------------------------------


_MAX_CONTACTS = 100
_RECENT_ACTIVITY = 25


async def _load_activity(
    session, lead_id: str, limit: int, before: datetime | None
) -> list[ActivityCallOut | ActivityEmailOut]:
    call_q = select(CallOutcome).where(CallOutcome.lead_id == lead_id)
    sent_q = select(SentLog).where(SentLog.lead_id == lead_id)
    if before is not None:
        call_q = call_q.where(CallOutcome.logged_at < before)
        sent_q = sent_q.where(SentLog.sent_at < before)
    calls = (await session.execute(call_q.order_by(desc(CallOutcome.logged_at)).limit(limit))).scalars().all()
    sents = (await session.execute(sent_q.order_by(desc(SentLog.sent_at)).limit(limit))).scalars().all()

    events: list[tuple[datetime, ActivityCallOut | ActivityEmailOut]] = []
    for c in calls:
        events.append(
            (
                c.logged_at,
                ActivityCallOut(outcome=c.outcome, logged_at=c.logged_at.isoformat(), note=c.note),
            )
        )
    for s_ in sents:
        events.append(
            (
                s_.sent_at,
                ActivityEmailOut(
                    subject=s_.subject,
                    to_email=s_.to_email,
                    sent_at=s_.sent_at.isoformat(),
                    replied_at=_iso(s_.replied_at),
                    mode=s_.mode,
                ),
            )
        )
    events.sort(key=lambda t: t[0], reverse=True)
    return [e[1] for e in events[:limit]]


@router.get("/{broker_id}", response_model=BrokerDetailOut)
async def get_broker(request: Request, broker_id: str) -> BrokerDetailOut:
    sessionmaker = request.app.state.sessionmaker
    today = _now().date()
    now = _now()

    async with sessionmaker() as s:
        lead = (await s.execute(select(Lead).where(Lead.id == broker_id, Lead.kind == "Broker"))).scalar_one_or_none()
        if lead is None:
            raise HTTPException(status_code=404, detail="broker not found")

        latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent = await _load_next_action_inputs(
            s, [lead.id], today
        )

        # Named contacts — cap at _MAX_CONTACTS, ordered by (DM DESC, confidence, provenance count DESC).
        contact_rows = (await s.execute(select(LeadContact).where(LeadContact.lead_id == lead.id))).scalars().all()
        prov_counts_rows = (
            await s.execute(
                select(LeadContactProvenance.contact_id, func.count())
                .where(LeadContactProvenance.contact_id.in_([c.id for c in contact_rows] or [-1]))
                .group_by(LeadContactProvenance.contact_id)
            )
        ).all()
        prov_counts: dict[int, int] = {cid: n for cid, n in prov_counts_rows}

        def _conf_rank(conf: str | None) -> int:
            return {"high": 0, "medium": 1, "low": 2}.get(conf or "", 3)

        contact_rows_sorted = sorted(
            contact_rows,
            key=lambda c: (
                0 if c.is_decision_maker else 1,
                _conf_rank(c.confidence),
                -prov_counts.get(c.id, 0),
            ),
        )[:_MAX_CONTACTS]

        # Timeline.
        activity = await _load_activity(s, lead.id, _RECENT_ACTIVITY, before=None)

        # Summary — 30-day counts.
        cutoff = now - timedelta(days=30)
        sent_30d = (
            await s.execute(
                select(func.count()).select_from(SentLog).where(SentLog.lead_id == lead.id, SentLog.sent_at >= cutoff)
            )
        ).scalar_one()
        reply_30d = (
            await s.execute(
                select(func.count())
                .select_from(SentLog)
                .where(
                    SentLog.lead_id == lead.id,
                    SentLog.replied_at.isnot(None),
                    SentLog.replied_at >= cutoff,
                )
            )
        ).scalar_one()

    lc = latest_call.get(lead.id)
    ls = latest_sent.get(lead.id)
    pcb = pending_cb.get(lead.id)
    a = agg.get(lead.id, _ContactAgg())
    action = _compute_action_for(
        lead,
        lc,
        ls,
        pcb,
        a,
        totals_call.get(lead.id, 0),
        totals_sent.get(lead.id, 0),
        now,
        today,
    )
    last_activity = None
    for cand in (lc.logged_at if lc else None, ls.sent_at if ls else None):
        if cand is None:
            continue
        if last_activity is None or cand > last_activity:
            last_activity = cand

    base = _row_out(lead, action, last_activity)
    detail = BrokerDetailBody(
        **base.model_dump(),
        address=_field(lead.address, lead.address_source),
        linkedin_company_url=lead.linkedin_company_url,
        website_url=lead.website_url,
        contacts=[
            NamedContactOut(
                id=c.id,
                name=_field(c.name, c.source, c.last_verified_at, c.confidence),
                title=_field(c.title, c.source, c.last_verified_at, c.confidence),
                email=_field(c.email, c.source, c.last_verified_at, c.confidence),
                phone=_field(c.phone, c.source, c.last_verified_at, c.confidence),
                is_decision_maker=c.is_decision_maker,
                pipeline_status=c.pipeline_status,
                sighted_count=prov_counts.get(c.id, 0),
                linkedin_url=c.linkedin_url,
            )
            for c in contact_rows_sorted
        ],
    )

    summary = BrokerSummaryOut(
        sent_count_30d=int(sent_30d or 0),
        reply_count_30d=int(reply_30d or 0),
        last_call=LastCallOut(outcome=lc.outcome, logged_at=lc.logged_at.isoformat()) if lc else None,
        last_email=(
            LastEmailOut(subject=ls.subject, sent_at=ls.sent_at.isoformat(), replied_at=_iso(ls.replied_at))
            if ls
            else None
        ),
    )
    return BrokerDetailOut(broker=detail, activity=activity, summary=summary)


# ---------- GET /brokers/{id}/activity ------------------------------------


@router.get("/{broker_id}/activity", response_model=ActivityPageOut)
async def get_broker_activity(
    request: Request,
    broker_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ActivityPageOut:
    sessionmaker = request.app.state.sessionmaker
    before: datetime | None = None
    if cursor:
        raw = _decode_cursor(cursor)
        try:
            before = datetime.fromisoformat(raw) if raw else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc

    async with sessionmaker() as s:
        exists = (
            await s.execute(select(Lead.id).where(Lead.id == broker_id, Lead.kind == "Broker"))
        ).scalar_one_or_none()
        if exists is None:
            raise HTTPException(status_code=404, detail="broker not found")

        # Fetch limit+1 so we know if a next page exists.
        events = await _load_activity(s, broker_id, limit + 1, before)

    next_cursor: str | None = None
    if len(events) > limit:
        events = events[:limit]
        last = events[-1]
        # The cursor is the "before" timestamp — ISO of the last item.
        last_at = last.logged_at if isinstance(last, ActivityCallOut) else last.sent_at
        next_cursor = _encode_cursor(last_at)
    return ActivityPageOut(items=events, next_cursor=next_cursor)
