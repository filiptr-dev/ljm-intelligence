"""Brokers API — thin router over ``app.prospecting.brokers_service``.

Three GET routes, all owner-only (wired at ``main.py``):

  GET  /brokers                — ranked list with contact summary + next-action chip.
  GET  /brokers/{id}           — full contact block + computed next action + timeline.
  GET  /brokers/{id}/activity  — paged timeline tail for the detail page.

Business logic (next-action rule table, DB input assembly, deterministic sort)
lives in the service. Cursor encoding stays here — it is HTTP sugar.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.prospecting.brokers_service import (
    ActivityCallEvent,
    ActivityEmailEvent,
    BrokerRowData,
    ContactField,
    NamedContactRow,
    NotFoundError,
    get_activity_page as svc_get_activity_page,
    get_detail as svc_get_detail,
    list_brokers as svc_list_brokers,
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


# ---------- cursor helpers --------------------------------------------------


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


# ---------- projection ------------------------------------------------------


def _field_out(f: ContactField) -> ContactFieldOut:
    return ContactFieldOut(**f.__dict__)


def _row_out(r: BrokerRowData) -> BrokerRowOut:
    return BrokerRowOut(
        id=r.id,
        name=r.name,
        mc=r.mc,
        dot=r.dot,
        state=r.state,
        city=r.city,
        phone=_field_out(r.phone),
        primary_email=_field_out(r.primary_email),
        fit_score=r.fit_score,
        next_action=NextActionOut(
            kind=r.next_action.kind,  # type: ignore[arg-type]
            reason=r.next_action.reason,
            due_at=r.next_action.due_at,
        ),
        last_activity_at=r.last_activity_at,
    )


def _event_out(ev: ActivityCallEvent | ActivityEmailEvent) -> ActivityCallOut | ActivityEmailOut:
    if isinstance(ev, ActivityCallEvent):
        return ActivityCallOut(outcome=ev.outcome, logged_at=ev.logged_at, note=ev.note)
    return ActivityEmailOut(
        subject=ev.subject,
        to_email=ev.to_email,
        sent_at=ev.sent_at,
        replied_at=ev.replied_at,
        mode=ev.mode,
    )


def _named_contact_out(c: NamedContactRow) -> NamedContactOut:
    return NamedContactOut(
        id=c.id,
        name=_field_out(c.name),
        title=_field_out(c.title),
        email=_field_out(c.email),
        phone=_field_out(c.phone),
        is_decision_maker=c.is_decision_maker,
        pipeline_status=c.pipeline_status,
        sighted_count=c.sighted_count,
        linkedin_url=c.linkedin_url,
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
    result = await svc_list_brokers(
        request.app.state.sessionmaker,
        state=state,
        min_fit=min_fit,
        has_email=has_email,
        has_phone=has_phone,
        next_action=next_action,
        q=q,
    )

    # Cursor is the lead id to start AFTER.
    start = 0
    if cursor:
        anchor = _decode_cursor(cursor)
        for i, keys in enumerate(result.sort_keys):
            if keys[3] == anchor:
                start = i + 1
                break

    page = result.items[start : start + limit]
    next_cursor = None
    if start + limit < len(result.items) and page:
        last_id = result.sort_keys[start + limit - 1][3]
        next_cursor = _encode_cursor(last_id)

    return BrokerListOut(
        items=[_row_out(r) for r in page],
        next_cursor=next_cursor,
        total=result.total,
    )


# ---------- GET /brokers/{id} ---------------------------------------------


@router.get("/{broker_id}", response_model=BrokerDetailOut)
async def get_broker(request: Request, broker_id: str) -> BrokerDetailOut:
    try:
        result = await svc_get_detail(request.app.state.sessionmaker, broker_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="broker not found") from exc

    base = _row_out(result.broker)
    detail = BrokerDetailBody(
        **base.model_dump(),
        address=_field_out(result.address),
        linkedin_company_url=result.linkedin_company_url,
        website_url=result.website_url,
        contacts=[_named_contact_out(c) for c in result.contacts],
    )
    summary = BrokerSummaryOut(
        sent_count_30d=result.summary.sent_count_30d,
        reply_count_30d=result.summary.reply_count_30d,
        last_call=LastCallOut(**result.summary.last_call.__dict__) if result.summary.last_call else None,
        last_email=LastEmailOut(**result.summary.last_email.__dict__) if result.summary.last_email else None,
    )
    return BrokerDetailOut(
        broker=detail,
        activity=[_event_out(e) for e in result.activity],
        summary=summary,
    )


# ---------- GET /brokers/{id}/activity ------------------------------------


@router.get("/{broker_id}/activity", response_model=ActivityPageOut)
async def get_broker_activity(
    request: Request,
    broker_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ActivityPageOut:
    before: datetime | None = None
    if cursor:
        raw = _decode_cursor(cursor)
        try:
            before = datetime.fromisoformat(raw) if raw else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc

    try:
        events, has_more = await svc_get_activity_page(
            request.app.state.sessionmaker, broker_id, limit=limit, before=before
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="broker not found") from exc

    next_cursor: str | None = None
    if has_more and events:
        last = events[-1]
        last_at = last.logged_at if isinstance(last, ActivityCallEvent) else last.sent_at
        next_cursor = _encode_cursor(last_at)

    return ActivityPageOut(items=[_event_out(e) for e in events], next_cursor=next_cursor)
