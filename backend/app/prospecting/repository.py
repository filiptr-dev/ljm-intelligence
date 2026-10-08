"""Prospecting — repository seam.

All SELECT calls for prospecting-owned aggregates (Lead, LeadContact,
LeadSource, Score). Previously inlined in `service.py`; moved here as the
single SQL seam. Services call these; routers call services. See the
plan: projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations

from typing import Protocol  # noqa: F401

from sqlalchemy import Select, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prospecting.models import Lead, LeadContact, LeadSource, Score


def leads_base_query(
    *,
    state: str | None = None,
    kind: str | None = None,
    min_score: int | None = None,
    q: str | None = None,
) -> Select:
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
    return base


async def count_leads(session: AsyncSession, base: Select) -> int:
    row = await session.execute(select(func.count()).select_from(base.subquery()))
    return int(row.scalar() or 0)


async def list_leads_page(
    session: AsyncSession, base: Select, *, offset: int, limit: int
) -> list[Lead]:
    rows = await session.execute(
        base.order_by(desc(Lead.last_seen_at), desc(Lead.id)).offset(offset).limit(limit)
    )
    return list(rows.scalars().all())


async def list_lead_sources(session: AsyncSession, lead_ids: list[str]) -> list[tuple[str, str]]:
    res = await session.execute(
        select(LeadSource.lead_id, LeadSource.source).where(LeadSource.lead_id.in_(lead_ids))
    )
    return list(res.all())


async def get_lead(session: AsyncSession, lead_id: str) -> Lead | None:
    res = await session.execute(select(Lead).where(Lead.id == lead_id))
    return res.scalar_one_or_none()


async def list_sources_for_lead(session: AsyncSession, lead_id: str) -> list[str]:
    rows = (
        await session.execute(select(LeadSource.source).where(LeadSource.lead_id == lead_id))
    ).scalars().all()
    return list(rows)


async def list_contacts_for_lead(session: AsyncSession, lead_id: str) -> list[LeadContact]:
    rows = (
        await session.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))
    ).scalars().all()
    return list(rows)


async def latest_score_for_lead(session: AsyncSession, lead_id: str) -> Score | None:
    return (
        await session.execute(
            select(Score).where(Score.lead_id == lead_id).order_by(desc(Score.created_at)).limit(1)
        )
    ).scalar_one_or_none()
