"""Call List API — the operator's daily phone queue.

Three routes, all read-heavy, no Gemini in the read path (see plan §"Rules that apply"):

  GET  /tools/call-list                — today's ranked ≤25, deterministic
  POST /tools/call-list/outcome        — log an outcome, return the updated list
  GET  /tools/call-list/history        — a single lead's outcome timeline

The ranking lives in `app.pipeline.call_rank` — this module is a thin adapter that
pulls the three inputs (leads, outcomes, open capacity posts) plus the small
`sent_log` roll-up for stale-relationship signal, hands them to the pure ranker,
and serializes the result.

Style mirrors `app/api/capacity.py`: `APIRouter(prefix=...)`, `request.app.state.sessionmaker`,
Pydantic response models, module-level `_serialize` helpers.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, func, select

from app.models import CallOutcome, CapacityPost, Lead, SentLog
from app.pipeline.call_rank import CALLBACK_DEFAULT_OFFSET_DAYS, CallRow, rank_call_list

router = APIRouter(prefix="/tools/call-list", tags=["call-list"])

_ALLOWED_OUTCOMES = ("booked", "callback", "not_interested", "no_answer")


# ---------- schemas ---------------------------------------------------------


class LastOutcomeOut(BaseModel):
    outcome: str
    logged_at: str | None


class CallRowOut(BaseModel):
    lead_id: str
    name: str
    state: str
    city: str | None
    phone: str
    primary_email: str | None
    score: int
    reasons: list[str]
    opener: str
    last_outcome: LastOutcomeOut | None


class CallListOut(BaseModel):
    date: str  # today, ISO
    items: list[CallRowOut]


class OutcomeIn(BaseModel):
    lead_id: str = Field(min_length=1, max_length=64)
    outcome: str
    callback_at: date | None = None
    note: str | None = None

    @field_validator("outcome")
    @classmethod
    def _check_outcome(cls, v: str) -> str:
        if v not in _ALLOWED_OUTCOMES:
            raise ValueError(f"outcome must be one of {_ALLOWED_OUTCOMES}")
        return v


class OutcomeHistoryRow(BaseModel):
    id: int
    outcome: str
    callback_at: str | None
    note: str | None
    logged_at: str
    logged_by: str | None


class OutcomeHistoryOut(BaseModel):
    lead_id: str
    items: list[OutcomeHistoryRow]


# ---------- serializers -----------------------------------------------------


def _row_out(r: CallRow) -> CallRowOut:
    return CallRowOut(
        lead_id=r.lead_id,
        name=r.name,
        state=r.state,
        city=r.city,
        phone=r.phone,
        primary_email=r.primary_email,
        score=r.score,
        reasons=list(r.reasons),
        opener=r.opener,
        last_outcome=(
            LastOutcomeOut(**r.last_outcome) if r.last_outcome else None
        ),
    )


def _history_row(o: CallOutcome) -> OutcomeHistoryRow:
    return OutcomeHistoryRow(
        id=o.id,
        outcome=o.outcome,
        callback_at=o.callback_at.isoformat() if o.callback_at else None,
        note=o.note,
        logged_at=o.logged_at.isoformat() if o.logged_at else "",
        logged_by=o.logged_by,
    )


# ---------- shared read -----------------------------------------------------


async def _load_and_rank(sessionmaker, today: date, limit: int) -> list[CallRow]:
    """Pull the three inputs + email touchpoints, hand to the pure ranker.

    The ranker enforces the phone-required rule and the drop rules — this fn is a
    dumb data pump so all the interesting logic stays testable in isolation.
    """
    async with sessionmaker() as s:
        # Leads with a phone — cheap pre-filter, the ranker also enforces it.
        leads = (
            (
                await s.execute(
                    select(Lead).where(Lead.phone.isnot(None)).where(Lead.phone != "")
                )
            )
            .scalars()
            .all()
        )
        outcomes = (await s.execute(select(CallOutcome))).scalars().all()
        posts = (
            (await s.execute(select(CapacityPost).where(CapacityPost.status == "open")))
            .scalars()
            .all()
        )
        # Latest email touchpoint per lead, as a date map — feeds the stale-relationship signal.
        email_rows = (
            await s.execute(
                select(SentLog.lead_id, func.max(SentLog.sent_at)).group_by(SentLog.lead_id)
            )
        ).all()
    last_email_by_lead: dict[str, date] = {}
    for lid, sent_at in email_rows:
        if sent_at is None:
            continue
        d = sent_at.date() if isinstance(sent_at, datetime) else sent_at
        last_email_by_lead[lid] = d

    return rank_call_list(
        leads,
        outcomes,
        posts,
        today,
        top_n=limit,
        last_email_by_lead=last_email_by_lead,
    )


def _today() -> date:
    # Isolated so tests can patch if they ever need to; UTC keeps the "today drop" rule
    # consistent with `logged_at` which is stored as timestamptz.
    return datetime.now(UTC).date()


# ---------- routes ----------------------------------------------------------


@router.get("", response_model=CallListOut)
async def get_call_list(
    request: Request, limit: int = Query(25, ge=1, le=100)
) -> CallListOut:
    today = _today()
    rows = await _load_and_rank(request.app.state.sessionmaker, today, limit)
    return CallListOut(date=today.isoformat(), items=[_row_out(r) for r in rows])


@router.post("/outcome", response_model=CallListOut)
async def log_outcome(request: Request, body: OutcomeIn) -> CallListOut:
    today = _today()
    sessionmaker = request.app.state.sessionmaker

    # For outcome=callback, default to +2 days when the client didn't set one
    # (see plan gate decision 3 — the constant lives in the ranker so the two agree).
    callback_at = body.callback_at
    if body.outcome == "callback" and callback_at is None:
        callback_at = today + timedelta(days=CALLBACK_DEFAULT_OFFSET_DAYS)

    async with sessionmaker() as s:
        exists = (
            await s.execute(select(Lead.id).where(Lead.id == body.lead_id))
        ).scalar_one_or_none()
        if not exists:
            raise HTTPException(404, "lead not found")

        row = CallOutcome(
            lead_id=body.lead_id,
            outcome=body.outcome,
            callback_at=callback_at,
            note=body.note,
            logged_at=datetime.now(UTC),
        )
        s.add(row)
        await s.commit()

    rows = await _load_and_rank(sessionmaker, today, 25)
    return CallListOut(date=today.isoformat(), items=[_row_out(r) for r in rows])


@router.get("/history", response_model=OutcomeHistoryOut)
async def get_history(
    request: Request,
    lead_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=200),
) -> OutcomeHistoryOut:
    async with request.app.state.sessionmaker() as s:
        exists = (
            await s.execute(select(Lead.id).where(Lead.id == lead_id))
        ).scalar_one_or_none()
        if not exists:
            raise HTTPException(404, "lead not found")
        rows = (
            (
                await s.execute(
                    select(CallOutcome)
                    .where(CallOutcome.lead_id == lead_id)
                    .order_by(desc(CallOutcome.logged_at))
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    return OutcomeHistoryOut(lead_id=lead_id, items=[_history_row(o) for o in rows])
