"""Leads API — thin router over ``app.prospecting.service``.

The business logic (filters, pagination, N+1-safe source fetch) lives in
``app.prospecting.service``. This router does HTTP: parse query params,
call the service, project the service's dataclasses into pydantic schemas.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.prospecting.service import list_leads as svc_list_leads
from app.prospecting.service import show_lead as svc_show_lead

router = APIRouter(prefix="/leads", tags=["leads"])


class LeadOut(BaseModel):
    id: str
    kind: str
    name: str
    state: str
    city: str | None = None
    mc: str | None = None
    dot: str | None = None
    domain: str | None = None
    primary_email: str | None = None
    phone: str | None = None
    current_score: int | None = None
    first_seen_at: str
    last_seen_at: str
    sources: list[str] = []


class LeadDetail(LeadOut):
    address: str | None = None
    raw: dict = {}
    evidence: dict = {}
    contacts: list[dict] = []
    score: dict | None = None


class LeadsPage(BaseModel):
    items: list[LeadOut]
    total: int
    limit: int
    offset: int


@router.get("", response_model=LeadsPage)
async def list_leads(
    request: Request,
    state: str | None = None,
    kind: str | None = None,
    min_score: int | None = Query(default=None, ge=0, le=100),
    q: str | None = None,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> LeadsPage:
    async with request.app.state.sessionmaker() as session:
        result = await svc_list_leads(
            session,
            state=state,
            kind=kind,
            min_score=min_score,
            q=q,
            offset=offset,
            limit=limit,
        )
    return LeadsPage(
        items=[LeadOut(**row.__dict__) for row in result.items],
        total=result.total,
        limit=result.limit,
        offset=result.offset,
    )


@router.get("/{lead_id}", response_model=LeadDetail)
async def show_lead(request: Request, lead_id: str) -> LeadDetail:
    async with request.app.state.sessionmaker() as session:
        row = await svc_show_lead(session, lead_id)
    if row is None:
        raise HTTPException(404, "lead not found")
    return LeadDetail(**row.__dict__)
