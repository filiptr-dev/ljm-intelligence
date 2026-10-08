"""Call List API — thin router over ``app.prospecting.call_list_service``.

Three routes, all read-heavy, no Gemini in the read path:

  GET  /tools/call-list                — today's ranked ≤25, deterministic
  POST /tools/call-list/outcome        — log an outcome, return the updated list
  GET  /tools/call-list/history        — a single lead's outcome timeline

The pure ranker lives in ``app.pipeline.call_rank``; the service holds the DB
plumbing and the "default callback = today+2d" rule.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, HTTPException, Path, Query, Request
from pydantic import BaseModel, Field, field_validator

from app.prospecting.call_list_service import (
    ALLOWED_OUTCOMES,
    LeadNotFoundError,
    OutcomeNotFoundError,
    delete_outcome as svc_delete_outcome,
    get_history as svc_get_history,
    load_and_rank as svc_load_and_rank,
    log_outcome as svc_log_outcome,
    today_utc,
)
from app.prospecting.pipeline.call_rank import CallRow

# Backwards-compat alias: ``app/api/overview.py`` imported ``_load_and_rank`` from
# here. Keep the name reachable so the overview route (and any ad-hoc importer)
# does not have to flip at the same time. The service is the source of truth.
_load_and_rank = svc_load_and_rank

router = APIRouter(prefix="/tools/call-list", tags=["call-list"])


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
    # Only present on POST /outcome responses — the id of the row we just
    # inserted. The FE's undo toast uses it to call DELETE /outcome/{id}.
    logged_outcome_id: int | None = None


class OutcomeIn(BaseModel):
    lead_id: str = Field(min_length=1, max_length=64)
    outcome: str
    callback_at: date | None = None
    note: str | None = None

    @field_validator("outcome")
    @classmethod
    def _check_outcome(cls, v: str) -> str:
        if v not in ALLOWED_OUTCOMES:
            raise ValueError(f"outcome must be one of {ALLOWED_OUTCOMES}")
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
        last_outcome=(LastOutcomeOut(**r.last_outcome) if r.last_outcome else None),
    )


@router.get("", response_model=CallListOut)
async def get_call_list(request: Request, limit: int = Query(25, ge=1, le=100)) -> CallListOut:
    today = today_utc()
    rows = await svc_load_and_rank(request.app.state.sessionmaker, today, limit)
    return CallListOut(date=today.isoformat(), items=[_row_out(r) for r in rows])


@router.post("/outcome", response_model=CallListOut)
async def log_outcome(request: Request, body: OutcomeIn) -> CallListOut:
    today = today_utc()
    try:
        result = await svc_log_outcome(
            request.app.state.sessionmaker,
            lead_id=body.lead_id,
            outcome=body.outcome,
            callback_at=body.callback_at,
            note=body.note,
            today=today,
        )
    except LeadNotFoundError as exc:
        raise HTTPException(404, "lead not found") from exc
    return CallListOut(
        date=today.isoformat(),
        items=[_row_out(r) for r in result.rows],
        logged_outcome_id=result.outcome_id,
    )


@router.delete("/outcome/{outcome_id}", response_model=CallListOut)
async def delete_outcome(
    request: Request,
    outcome_id: int = Path(..., ge=1, description="The CallOutcome.id to undo."),
) -> CallListOut:
    """Undo a just-logged outcome. 404 if the row is already gone (second click)."""
    today = today_utc()
    try:
        rows = await svc_delete_outcome(
            request.app.state.sessionmaker, outcome_id=outcome_id, today=today
        )
    except OutcomeNotFoundError as exc:
        raise HTTPException(404, "outcome not found") from exc
    return CallListOut(date=today.isoformat(), items=[_row_out(r) for r in rows])


@router.get("/history", response_model=OutcomeHistoryOut)
async def get_history(
    request: Request,
    lead_id: str = Query(..., min_length=1, max_length=64),
    limit: int = Query(50, ge=1, le=200),
) -> OutcomeHistoryOut:
    async with request.app.state.sessionmaker() as s:
        try:
            result = await svc_get_history(s, lead_id=lead_id, limit=limit)
        except LeadNotFoundError as exc:
            raise HTTPException(404, "lead not found") from exc
    return OutcomeHistoryOut(
        lead_id=result.lead_id,
        items=[OutcomeHistoryRow(**r.__dict__) for r in result.items],
    )
