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

from app.shared.db import AsyncSession

from app.prospecting.models import Lead
from app.prospecting.repository import (
    count_leads,
    get_lead,
    latest_score_for_lead,
    leads_base_query,
    list_contacts_for_lead,
    list_lead_sources,
    list_leads_page,
    list_sources_for_lead,
)


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
    base = leads_base_query(state=state, kind=kind, min_score=min_score, q=q)
    total = await count_leads(session, base)
    leads = await list_leads_page(session, base, offset=offset, limit=limit)

    source_map: dict[str, list[str]] = {}
    if leads:
        for lid, src in await list_lead_sources(session, [lead.id for lead in leads]):
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
    l = await get_lead(session, lead_id)
    if not l:
        return None
    srcs = await list_sources_for_lead(session, lead_id)
    contacts = await list_contacts_for_lead(session, lead_id)
    score_row = await latest_score_for_lead(session, lead_id)
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
