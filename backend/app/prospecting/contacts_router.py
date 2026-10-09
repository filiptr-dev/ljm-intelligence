"""Contacts routes — thin router over ``contacts_service`` + ``contacts_repository``.

Three endpoints behind one ``/contacts`` prefix plus the per-lead view:

  GET  /leads/{lead_id}/contacts            — list for one lead
  POST /leads/{lead_id}/contacts/refresh    — refresh (sync or enqueued job)
  GET  /contacts?role=freight_manager&…     — cross-lead segment feed

Business logic (dedupe, trust uplift, segment match) lives in the service +
repo. This router parses, calls, returns the DTO.
"""

from __future__ import annotations

import logging
from datetime import UTC

from fastapi import APIRouter, HTTPException, Query, Request

from app.prospecting.contacts_schemas import (
    ContactListOut,
    ContactOut,
    RefreshIn,
    RefreshOut,
)
from app.prospecting.contacts_service import (
    FREIGHT_TITLES,
    contact_to_out,
    list_segment_freight_managers,
    promote_from_sources,
)

log = logging.getLogger(__name__)

router = APIRouter(tags=["contacts"])


@router.get("/leads/{lead_id}/contacts", response_model=ContactListOut)
async def list_lead_contacts(lead_id: str, request: Request) -> ContactListOut:
    """List all contacts for a single lead. Keyset-less (small N per lead)."""
    sm = request.app.state.sessionmaker
    async with sm() as s:
        from app.prospecting.contacts_repository import SqlLeadContactRepo

        repo = SqlLeadContactRepo(s)
        rows = await repo.by_lead(lead_id)
        counts = {c.id: await repo.provenance_count(c.id) for c in rows}
    items = [ContactOut(**contact_to_out(c, evidence_count=counts[c.id])) for c in rows]
    return ContactListOut(items=items, next_cursor=None)


@router.post("/leads/{lead_id}/contacts/refresh", response_model=RefreshOut)
async def refresh_lead_contacts(
    lead_id: str, payload: RefreshIn, request: Request
) -> RefreshOut:
    """Run the four-source promotion for a lead. v1: inline (sync).

    The procrastinate enqueue path exists via ``shared.queue.dispatch`` but
    inline keeps the UX honest — the composer page shows the fresh rows on
    reload immediately. The job seam is already wired for campaign sends.
    """
    sm = request.app.state.sessionmaker
    settings = request.app.state.settings
    from sqlalchemy import select as _select

    from app.integrations.adapters.ai.provider import get_for
    from app.prospecting.contacts_adapters import (
        FmcsaSnapshotAdapter,
        GeminiDecisionMakersAdapter,
        InboxSignatureAdapter,
        SiteScrapeAdapter,
    )
    from app.prospecting.contacts_repository import SqlLeadContactRepo
    from app.prospecting.models import Lead

    sources = set(payload.sources or ["fmcsa", "gemini", "site", "inbox"])

    async with sm() as s:
        lead = (await s.execute(_select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
        if lead is None:
            raise HTTPException(404, "lead not found")

        # Cache helpers for the FMCSA snapshot.
        from datetime import datetime, timedelta

        from app.prospecting.models import FmcsaSnapshotCache

        async def cache_fetch(dot: str) -> dict | None:
            row = (
                await s.execute(_select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == dot))
            ).scalar_one_or_none()
            if row is None:
                return None
            if row.fetched_at < datetime.now(UTC) - timedelta(days=30):
                return None
            return row.payload or {}

        async def cache_write(dot: str, payload: dict) -> None:
            existing = (
                await s.execute(_select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == dot))
            ).scalar_one_or_none()
            if existing:
                existing.payload = payload
                existing.fetched_at = datetime.now(UTC)
            else:
                s.add(FmcsaSnapshotCache(dot=dot, payload=payload))
            await s.flush()

        provider = get_for("enrichment", settings=settings)
        fetcher = getattr(request.app.state, "fetcher", None)
        http = getattr(request.app.state, "http", None)

        adapters: list = []
        if "fmcsa" in sources:
            adapters.append(FmcsaSnapshotAdapter(client=http, cache_fetch=cache_fetch, cache_write=cache_write))
        if "gemini" in sources:
            adapters.append(GeminiDecisionMakersAdapter(provider=provider))
        if "site" in sources and fetcher is not None:
            adapters.append(SiteScrapeAdapter(provider=provider, fetcher=fetcher))
        if "inbox" in sources:
            adapters.append(InboxSignatureAdapter())

        repo = SqlLeadContactRepo(s)
        result = await promote_from_sources(repo, lead, adapters=adapters)
        await s.commit()
    return RefreshOut(job_id=None, status="ran_inline", created=result.created, updated=result.updated)


@router.get("/contacts", response_model=ContactListOut)
async def segment_feed(
    request: Request,
    role: str = Query("freight_manager"),
    cursor: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
) -> ContactListOut:
    if role != "freight_manager":
        raise HTTPException(400, "unknown role (only freight_manager supported in v1)")
    sm = request.app.state.sessionmaker
    async with sm() as s:
        from app.prospecting.contacts_repository import SqlLeadContactRepo

        repo = SqlLeadContactRepo(s)
        rows, next_cursor = await list_segment_freight_managers(repo, cursor=cursor, limit=limit)
        counts = {c.id: await repo.provenance_count(c.id) for c in rows}
    items = [ContactOut(**contact_to_out(c, evidence_count=counts[c.id])) for c in rows]
    return ContactListOut(items=items, next_cursor=next_cursor)


__all__ = ["FREIGHT_TITLES", "router"]
