"""Leads API: paged, filter-safe reads from stored data. No live Gemini on read."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, func, select

from app.models import Lead, LeadContact, LeadSource, Score

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
    async with request.app.state.sessionmaker() as s:
        base = select(Lead)
        if state:
            base = base.where(Lead.state == state.upper())
        if kind:
            base = base.where(Lead.kind == kind)
        if min_score is not None:
            base = base.where(Lead.current_score >= min_score)
        if q:
            like = f"%{q}%"
            base = base.where(Lead.name.ilike(like))

        total_res = await s.execute(select(func.count()).select_from(base.subquery()))
        total = int(total_res.scalar() or 0)

        rows = await s.execute(
            base.order_by(desc(Lead.last_seen_at), desc(Lead.id)).offset(offset).limit(limit)
        )
        leads = list(rows.scalars().all())

        # Batch-fetch sources per lead so /leads doesn't N+1.
        source_map: dict[str, list[str]] = {}
        if leads:
            src_rows = await s.execute(
                select(LeadSource.lead_id, LeadSource.source).where(
                    LeadSource.lead_id.in_([lead.id for lead in leads])
                )
            )
            for lid, src in src_rows.all():
                source_map.setdefault(lid, [])
                if src not in source_map[lid]:
                    source_map[lid].append(src)

    items = [
        LeadOut(
            id=l.id,
            kind=l.kind,
            name=l.name,
            state=l.state,
            city=l.city,
            mc=l.mc,
            dot=l.dot,
            domain=l.domain,
            primary_email=l.primary_email,
            phone=l.phone,
            current_score=l.current_score,
            first_seen_at=l.first_seen_at.isoformat() if l.first_seen_at else "",
            last_seen_at=l.last_seen_at.isoformat() if l.last_seen_at else "",
            sources=source_map.get(l.id, []),
        )
        for l in leads
    ]
    return LeadsPage(items=items, total=total, limit=limit, offset=offset)


@router.get("/{lead_id}", response_model=LeadDetail)
async def show_lead(request: Request, lead_id: str) -> LeadDetail:
    async with request.app.state.sessionmaker() as s:
        res = await s.execute(select(Lead).where(Lead.id == lead_id))
        l = res.scalar_one_or_none()
        if not l:
            raise HTTPException(404, "lead not found")
        srcs = (
            await s.execute(select(LeadSource.source).where(LeadSource.lead_id == lead_id))
        ).scalars().all()
        contacts = (
            await s.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))
        ).scalars().all()
        score_row = (
            await s.execute(
                select(Score).where(Score.lead_id == lead_id).order_by(desc(Score.created_at)).limit(1)
            )
        ).scalar_one_or_none()
    return LeadDetail(
        id=l.id,
        kind=l.kind,
        name=l.name,
        state=l.state,
        city=l.city,
        mc=l.mc,
        dot=l.dot,
        domain=l.domain,
        primary_email=l.primary_email,
        phone=l.phone,
        current_score=l.current_score,
        first_seen_at=l.first_seen_at.isoformat() if l.first_seen_at else "",
        last_seen_at=l.last_seen_at.isoformat() if l.last_seen_at else "",
        sources=list(dict.fromkeys(srcs)),
        address=l.address,
        raw=l.raw or {},
        evidence=l.evidence or {},
        contacts=[
            {
                "id": c.id,
                "name": c.name,
                "title": c.title,
                "email": c.email,
                "phone": c.phone,
                "source": c.source,
            }
            for c in contacts
        ],
        score={
            "value": score_row.score,
            "rationale": score_row.rationale,
            "signals": (score_row.signals or {}).get("signals", []),
            "model": score_row.model,
        }
        if score_row
        else None,
    )
