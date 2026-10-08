"""Outreach — repository seam.

All SQL for outreach-owned aggregates lives here. Services depend on these
functions, not on `select()` or `AsyncSession` queries directly. The 4
`select(` call sites previously inside `service.py` now live below —
`service.py` just orchestrates. See the plan:
projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations

from datetime import datetime
from typing import Protocol  # noqa: F401

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.identity.models import SettingsRow
from app.outreach.models import SentLog, Suppression
from app.prospecting.models import Lead, LeadContact


async def get_settings_row(s: AsyncSession) -> SettingsRow | None:
    return (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()


async def count_sent_since(s: AsyncSession, since: datetime) -> int:
    row = await s.execute(select(func.count()).select_from(SentLog).where(SentLog.sent_at >= since))
    return int(row.scalar_one() or 0)


async def list_outreach_candidates(
    s: AsyncSession, *, status_filter: str, min_fit: float
) -> list[LeadContact]:
    stmt = (
        select(LeadContact)
        .join(Lead, Lead.id == LeadContact.lead_id)
        .where(LeadContact.pipeline_status == status_filter)
        .where(LeadContact.email.is_not(None))
        .where(Lead.fit_score.is_not(None))
        .where(Lead.fit_score >= min_fit)
        .order_by(Lead.fit_score.desc(), LeadContact.id)
    )
    return list((await s.execute(stmt)).scalars().all())


async def list_suppressed_emails(s: AsyncSession) -> set[str]:
    rows = (await s.execute(select(Suppression.email))).scalars().all()
    return {e.lower() for e in rows if e}
