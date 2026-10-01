"""Prospecting service — DB-shaped reads for leads/brokers/shippers.

This module is the extracted business logic the thin ``app/api/leads.py``
router now delegates to. The router's job is HTTP: parse query params,
call ``list_leads()`` or ``show_lead()``, return the pydantic schema. The
service's job is DDD: open a session, read, project into dataclasses the
router can serialise without knowing SQL.

No FastAPI here. No pydantic. Returns plain dataclasses (and dict-shaped
field blobs for `evidence`/`raw`) so the service is reusable by jobs
(cron / procrastinate workers) that don't live under an HTTP request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Lead, LeadContact, LeadSource, Score


@dataclass
class LeadRow:
    id: str
    kind: str
    name: str
    state: str
    city: str | None
    mc: str | None
    dot: str | None
    domain: str | None
    primary_email: str | None
    phone: str | None
    current_score: int | None
    first_seen_at: str
    last_seen_at: str
    sources: list[str] = field(default_factory=list)


@dataclass
class LeadDetailRow(LeadRow):
    address: str | None = None
    raw: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    contacts: list[dict] = field(default_factory=list)
    score: dict | None = None


@dataclass
class LeadsPageResult:
    items: list[LeadRow]
    total: int
    limit: int
    offset: int


def _iso(dt: Any) -> str:
    return dt.isoformat() if dt else ""


def _lead_to_row(l: Lead, sources: list[str]) -> LeadRow:
    return LeadRow(
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
        first_seen_at=_iso(l.first_seen_at),
        last_seen_at=_iso(l.last_seen_at),
        sources=sources,
    )


async def list_leads(
    session: AsyncSession,
    *,
    state: str | None = None,
    kind: str | None = None,
    min_score: int | None = None,
    q: str | None = None,
    offset: int = 0,
    limit: int = 50,
) -> LeadsPageResult:
    """Paged, filtered list. Reuses the N+1-safe batch source fetch."""
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

    total_res = await session.execute(select(func.count()).select_from(base.subquery()))
    total = int(total_res.scalar() or 0)

    rows = await session.execute(
        base.order_by(desc(Lead.last_seen_at), desc(Lead.id)).offset(offset).limit(limit)
    )
    leads = list(rows.scalars().all())

    source_map: dict[str, list[str]] = {}
    if leads:
        src_rows = await session.execute(
            select(LeadSource.lead_id, LeadSource.source).where(
                LeadSource.lead_id.in_([lead.id for lead in leads])
            )
        )
        for lid, src in src_rows.all():
            source_map.setdefault(lid, [])
            if src not in source_map[lid]:
                source_map[lid].append(src)

    return LeadsPageResult(
        items=[_lead_to_row(l, source_map.get(l.id, [])) for l in leads],
        total=total,
        limit=limit,
        offset=offset,
    )


async def show_lead(session: AsyncSession, lead_id: str) -> LeadDetailRow | None:
    res = await session.execute(select(Lead).where(Lead.id == lead_id))
    l = res.scalar_one_or_none()
    if not l:
        return None
    srcs = (
        await session.execute(select(LeadSource.source).where(LeadSource.lead_id == lead_id))
    ).scalars().all()
    contacts = (
        await session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))
    ).scalars().all()
    score_row = (
        await session.execute(
            select(Score).where(Score.lead_id == lead_id).order_by(desc(Score.created_at)).limit(1)
        )
    ).scalar_one_or_none()
    row = _lead_to_row(l, list(dict.fromkeys(srcs)))
    return LeadDetailRow(
        **row.__dict__,
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
        score=(
            {
                "value": score_row.score,
                "rationale": score_row.rationale,
                "signals": (score_row.signals or {}).get("signals", []),
                "model": score_row.model,
            }
            if score_row
            else None
        ),
    )
