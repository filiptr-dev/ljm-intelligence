"""Shipper Finder service — rank, promote, detail.

Thin business layer behind ``app/api/shipper_finder.py``. The pure ranking
rules live in ``app.pipeline.shipper_rank``; this module pumps the three
inputs (candidates, open capacity posts, call outcomes) into it, folds in
per-candidate ``fit_score``/``fit_reasons`` from the DB, and handles the
transactional promote write.

Cursor encoding stays in the router — it is HTTP sugar, not DDD.
Returns plain dataclasses; raises :class:`NotFoundError` so the router can
map to HTTP 404.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CallOutcome, CapacityPost, Lead, ShipperCandidate
from app.pipeline.shipper_rank import (
    SHIPPER_FINDER_LIMIT_CAP,
    ShipperFilters,
    ShipperRow,
    rank_shippers,
)


class NotFoundError(Exception):
    """Raised when a shipper candidate id does not exist."""


@dataclass
class RankedListResult:
    rows: list[ShipperRow]
    fit_by_id: dict[str, tuple[int | None, list[str]]] = field(default_factory=dict)


@dataclass
class PromoteResult:
    lead_id: str
    created: bool


@dataclass
class PromotedLeadRow:
    id: str
    name: str
    state: str
    city: str | None
    kind: str
    mc: str | None
    dot: str | None
    domain: str | None
    phone: str | None
    primary_email: str | None


@dataclass
class ShipperDetailResult:
    row: ShipperRow
    evidence: dict | None
    osm_tags: dict | None
    raw: dict | None
    osm_ref: str | None
    fmcsa_mc: str | None
    fmcsa_dot: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    fit_score: int | None
    fit_reasons: list[str]
    promoted_lead: PromotedLeadRow | None
    # True when the ranker's drop rules excluded this row — the router then
    # falls back to a raw projection (score=0, no reasons) rather than hiding
    # the record from the detail page.
    dropped_by_ranker: bool = False
    candidate: ShipperCandidate | None = None


# ---------- shared read -----------------------------------------------------


async def rank_all(
    sessionmaker: Any,
    *,
    state: str | None,
    source: str | None,
    min_score: int | None,
    promoted_only: bool | None,
    q: str | None,
) -> RankedListResult:
    """Load candidates + posts + outcomes, hand to the pure ranker + fold in
    ``fit_score``/``fit_reasons`` for the full ranked set.

    Only ``state`` is pushed into SQL (backed by ``shipper_candidates_state``);
    the rest of the filtering happens in the ranker so the semantics match the
    unit tests. At demo scale (thousands of rows total) in-memory rank is
    cheap; if this ever grows we would add DB-side pre-filters — the ranker
    stays the source of truth for what "matching" means.
    """
    async with sessionmaker() as s:
        cand_q = select(ShipperCandidate)
        if state:
            cand_q = cand_q.where(ShipperCandidate.state == state.upper())
        candidates = (await s.execute(cand_q)).scalars().all()
        posts = (
            await s.execute(select(CapacityPost).where(CapacityPost.status == "open"))
        ).scalars().all()
        outcomes = (await s.execute(select(CallOutcome))).scalars().all()

    filters = ShipperFilters(
        state=state,
        source=source,
        min_score=min_score,
        promoted_only=promoted_only,
        q=q,
        # Rank the whole matching set up to the hard cap so cursor pagination
        # is stable across pages. The route-level ``limit`` is applied AFTER
        # cursor slicing so page N returns exactly N rows even when N < cap.
        limit=SHIPPER_FINDER_LIMIT_CAP,
    )
    today = datetime.now(UTC).date()
    rows = rank_shippers(candidates, posts, outcomes, today, filters=filters)

    fit_by_id: dict[str, tuple[int | None, list[str]]] = {}
    all_ids = [r.id for r in rows]
    if all_ids:
        async with sessionmaker() as s:
            fit_rows = (
                await s.execute(
                    select(
                        ShipperCandidate.id,
                        ShipperCandidate.fit_score,
                        ShipperCandidate.fit_reasons,
                    ).where(ShipperCandidate.id.in_(all_ids))
                )
            ).all()
        for cid, fs, fr in fit_rows:
            fit_by_id[cid] = (fs, list(fr or []))

    return RankedListResult(rows=rows, fit_by_id=fit_by_id)


# ---------- promote --------------------------------------------------------


def _mint_lead_id(c: ShipperCandidate) -> str:
    """Prefix-encoded id. Order matches the dedupe ladder: MC → DOT → DOMAIN →
    SHIPPER-<uuid>. The random fallback is only reached when a candidate has
    neither authority nor a domain (OSM-only rows) — those still deserve a
    promotable lead."""
    mc = c.fmcsa_mc or c.mc
    dot = c.fmcsa_dot or c.dot
    if mc:
        return f"MC-{mc}"
    if dot:
        return f"DOT-{dot}"
    if c.domain:
        return f"DOMAIN-{c.domain}"
    return f"SHIPPER-{uuid.uuid4().hex[:20]}"


async def _find_existing_lead(session: AsyncSession, c: ShipperCandidate) -> Lead | None:
    """Dedupe ladder: MC → DOT → domain → case-insensitive (name, state)."""
    mc = c.fmcsa_mc or c.mc
    if mc:
        row = (await session.execute(select(Lead).where(Lead.mc == mc))).scalar_one_or_none()
        if row:
            return row
    dot = c.fmcsa_dot or c.dot
    if dot:
        row = (await session.execute(select(Lead).where(Lead.dot == dot))).scalar_one_or_none()
        if row:
            return row
    if c.domain:
        row = (
            await session.execute(select(Lead).where(Lead.domain == c.domain))
        ).scalar_one_or_none()
        if row:
            return row
    if c.name and c.state:
        row = (
            await session.execute(
                select(Lead).where(
                    func.lower(Lead.name) == c.name.strip().lower(),
                    Lead.state == c.state.upper(),
                )
            )
        ).scalar_one_or_none()
        if row:
            return row
    return None


async def promote(sessionmaker: Any, candidate_id: str) -> PromoteResult:
    """Idempotent promote. One transaction, two tables. See plan §"Promote = DB transaction"."""
    async with sessionmaker() as s:  # noqa: SIM117 — inner s.begin() needs the bound session
        async with s.begin():
            c = (
                await s.execute(
                    select(ShipperCandidate).where(ShipperCandidate.id == candidate_id)
                )
            ).scalar_one_or_none()
            if c is None:
                raise NotFoundError("shipper candidate not found")

            # Idempotent short-circuit — a candidate already pointing at a lead
            # returns the existing link, never a new lead.
            if c.promoted_lead_id:
                from app.pipeline.enrichment import copy_enrichment_candidates_to_lead

                await copy_enrichment_candidates_to_lead(
                    s, candidate_id=c.id, lead_id=c.promoted_lead_id, run_id=None
                )
                return PromoteResult(lead_id=c.promoted_lead_id, created=False)

            existing = await _find_existing_lead(s, c)
            if existing is not None:
                c.promoted_lead_id = existing.id
                from app.pipeline.enrichment import copy_enrichment_candidates_to_lead

                await copy_enrichment_candidates_to_lead(
                    s, candidate_id=c.id, lead_id=existing.id, run_id=None
                )
                return PromoteResult(lead_id=existing.id, created=False)

            lead_id = _mint_lead_id(c)
            # Defensive: the mint could collide on a re-promote race — treat a
            # concurrent id hit as an idempotent link (created=false).
            already = (
                await s.execute(select(Lead).where(Lead.id == lead_id))
            ).scalar_one_or_none()
            if already is not None:
                c.promoted_lead_id = already.id
                return PromoteResult(lead_id=already.id, created=False)

            lead = Lead(
                id=lead_id,
                mc=(c.fmcsa_mc or c.mc),
                dot=(c.fmcsa_dot or c.dot),
                domain=c.domain,
                name=c.name,
                kind="Shipper",
                state=c.state.upper(),
                city=c.city,
                address=c.address,
                phone=c.phone,
                primary_email=c.primary_email,
                raw=c.raw or {},
                evidence=c.evidence or {},
                recommendations=[],
            )
            s.add(lead)
            c.promoted_lead_id = lead_id
            await s.flush()
            from app.pipeline.enrichment import copy_enrichment_candidates_to_lead

            await copy_enrichment_candidates_to_lead(
                s, candidate_id=c.id, lead_id=lead_id, run_id=None
            )
            return PromoteResult(lead_id=lead_id, created=True)


# ---------- detail --------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def get_detail(sessionmaker: Any, candidate_id: str) -> ShipperDetailResult:
    async with sessionmaker() as s:
        c = (
            await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == candidate_id))
        ).scalar_one_or_none()
        if c is None:
            raise NotFoundError("shipper candidate not found")

        posts = (
            await s.execute(select(CapacityPost).where(CapacityPost.status == "open"))
        ).scalars().all()
        outcomes = (await s.execute(select(CallOutcome))).scalars().all()

        promoted_lead: Lead | None = None
        if c.promoted_lead_id:
            promoted_lead = (
                await s.execute(select(Lead).where(Lead.id == c.promoted_lead_id))
            ).scalar_one_or_none()

    today = datetime.now(UTC).date()
    ranked = rank_shippers(
        [c], posts, outcomes, today, filters=ShipperFilters(limit=SHIPPER_FINDER_LIMIT_CAP)
    )
    row = ranked[0] if ranked else None
    dropped = row is None

    promoted_out: PromotedLeadRow | None = None
    if promoted_lead is not None:
        promoted_out = PromotedLeadRow(
            id=promoted_lead.id,
            name=promoted_lead.name,
            state=promoted_lead.state,
            city=promoted_lead.city,
            kind=promoted_lead.kind,
            mc=promoted_lead.mc,
            dot=promoted_lead.dot,
            domain=promoted_lead.domain,
            phone=promoted_lead.phone,
            primary_email=promoted_lead.primary_email,
        )

    return ShipperDetailResult(
        row=row if row is not None else _bare_row(c),
        evidence=c.evidence,
        osm_tags=c.osm_tags,
        raw=c.raw,
        osm_ref=c.osm_ref,
        fmcsa_mc=c.fmcsa_mc,
        fmcsa_dot=c.fmcsa_dot,
        first_seen_at=_iso(c.first_seen_at),
        last_seen_at=_iso(c.last_seen_at),
        fit_score=c.fit_score,
        fit_reasons=list(c.fit_reasons or []),
        promoted_lead=promoted_out,
        dropped_by_ranker=dropped,
        candidate=c,
    )


def _bare_row(c: ShipperCandidate) -> ShipperRow:
    """Projection used when the ranker's drop rules excluded this candidate.

    Returns a zero-scored ``ShipperRow`` with the raw fields so the detail
    page still shows the evidence trail.
    """
    return ShipperRow(
        id=c.id,
        name=c.name or "",
        state=(c.state or "").upper(),
        city=c.city,
        address=c.address,
        lat=c.lat,
        lng=c.lng,
        sources=tuple(c.sources or []),
        mc=c.mc,
        dot=c.dot,
        domain=c.domain,
        phone=c.phone,
        primary_email=c.primary_email,
        score=0,
        reasons=tuple(),
        promoted_lead_id=c.promoted_lead_id,
        match_reason=c.match_reason,
    )
