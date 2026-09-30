"""Shipper Finder API — direct-shipper tool (Slice 3).

Three routes, all thin adapters around the pure `pipeline.shipper_rank.rank_shippers`:

  GET  /tools/shipper-finder           — filtered, ranked, cursor-paginated
  POST /tools/shipper-finder/promote   — one-write gate into `leads` (idempotent)
  GET  /tools/shipper-finder/{id}      — single-candidate detail (right column)

Reads only; no Gemini in the read path (plan §"Rules that apply"). The ranker
owns the scoring / drop / filter logic; this module pumps the three inputs
(candidates + open capacity posts + call outcomes) into it, applies opaque-cursor
pagination on the ranked list, and serialises the result.

Cursor shape: URL-safe base64 of ``"{score}:{id}"``. The ranked order is
``(-score, id asc)`` so "after cursor (cs, cid)" means the row with either
``score < cs`` or ``(score == cs AND id > cid)``. Deterministic across pages
whenever the underlying data is unchanged.

Promote dedupe ladder (plan §"Promote route"): MC → DOT → domain → case-insensitive
``(name, state)``. Uses the candidate's ``fmcsa_mc``/``fmcsa_dot``/``domain``/
``name``/``state`` to reach into ``leads``. Idempotent: a second call on the same
candidate returns ``created=false`` with the previously linked ``lead_id``, and
the DB always has one lead, not two. The whole write runs inside
``async with s.begin():`` — two tables in one transaction (see plan rule
"Promote = DB transaction"; half-writes must not happen).

Style mirrors `app/api/call_list.py`: APIRouter, `request.app.state.sessionmaker`,
Pydantic response models with explicit ``response_model=…`` on every route so the
OpenAPI schema names (``ShipperListOut``, ``PromoteOut``, ``ShipperDetailOut``)
stay stable for the frontend typed client.
"""

from __future__ import annotations

import base64
import binascii
import uuid
from datetime import UTC, datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.models import CallOutcome, CapacityPost, Lead, ShipperCandidate
from app.pipeline.shipper_rank import (
    SHIPPER_FINDER_LIMIT_CAP,
    ShipperFilters,
    ShipperRow,
    rank_shippers,
)

router = APIRouter(prefix="/tools/shipper-finder", tags=["shipper-finder"])


# ---------- schemas ---------------------------------------------------------


class ShipperRowOut(BaseModel):
    id: str
    name: str
    state: str
    city: str | None
    address: str | None
    lat: float | None
    lng: float | None
    sources: list[str]
    mc: str | None
    dot: str | None
    domain: str | None
    phone: str | None
    primary_email: str | None
    score: int
    reasons: list[str]
    promoted_lead_id: str | None
    match_reason: str | None
    # Fit-score (scope change 2026-09-30). Null if never computed.
    fit_score: int | None = None
    fit_reasons: list[str] = Field(default_factory=list)


class ShipperListOut(BaseModel):
    items: list[ShipperRowOut]
    next_cursor: str | None = None


class PromoteIn(BaseModel):
    candidate_id: str = Field(min_length=1, max_length=64)


class PromoteOut(BaseModel):
    lead_id: str
    created: bool


class PromotedLeadOut(BaseModel):
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


class ShipperDetailOut(BaseModel):
    row: ShipperRowOut
    evidence: dict | None
    osm_tags: dict | None
    raw: dict | None
    osm_ref: str | None
    fmcsa_mc: str | None
    fmcsa_dot: str | None
    first_seen_at: str | None
    last_seen_at: str | None
    promoted_lead: PromotedLeadOut | None


# ---------- serializers -----------------------------------------------------


def _row_out(r: ShipperRow, *, fit_score: int | None = None, fit_reasons: list[str] | None = None) -> ShipperRowOut:
    return ShipperRowOut(
        id=r.id,
        name=r.name,
        state=r.state,
        city=r.city,
        address=r.address,
        lat=r.lat,
        lng=r.lng,
        sources=list(r.sources),
        mc=r.mc,
        dot=r.dot,
        domain=r.domain,
        phone=r.phone,
        primary_email=r.primary_email,
        score=r.score,
        reasons=list(r.reasons),
        promoted_lead_id=r.promoted_lead_id,
        match_reason=r.match_reason,
        fit_score=fit_score,
        fit_reasons=list(fit_reasons or []),
    )


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------- cursor helpers --------------------------------------------------


def _encode_cursor(score: int, id_: str) -> str:
    raw = f"{score}:{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    """Decode ``base64(score:id)``. Raises ``HTTPException(400)`` on garbage.

    Malformed cursors are a client bug (a stale link, a hand-typed value); the
    caller should surface it clearly, not silently return the first page.
    """
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad).decode("ascii")
        score_s, id_ = raw.split(":", 1)
        return int(score_s), id_
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"invalid cursor: {exc}") from exc


def _after_cursor(rows: list[ShipperRow], cursor: tuple[int, str]) -> list[ShipperRow]:
    """Return rows strictly after (score, id) in the ranked order (-score, id asc)."""
    cs, cid = cursor
    return [r for r in rows if (r.score < cs) or (r.score == cs and r.id > cid)]


# ---------- shared read -----------------------------------------------------


async def _rank_all(
    sessionmaker,
    *,
    state: str | None,
    source: str | None,
    min_score: int | None,
    promoted_only: bool | None,
    q: str | None,
) -> list[ShipperRow]:
    """Load candidates + posts + outcomes, hand to the pure ranker.

    Only ``state`` is pushed down to SQL (backed by ``shipper_candidates_state``);
    everything else is applied in the ranker so the filter semantics match the
    unit tests. At demo scale (thousands of rows total) an in-memory rank is
    cheap; if this ever grows we'd add DB-side pre-filters — the ranker stays
    the source of truth for what "matching" means.
    """
    async with sessionmaker() as s:
        cand_q = select(ShipperCandidate)
        if state:
            cand_q = cand_q.where(ShipperCandidate.state == state.upper())
        candidates = (await s.execute(cand_q)).scalars().all()
        posts = (await s.execute(select(CapacityPost).where(CapacityPost.status == "open"))).scalars().all()
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
    return rank_shippers(candidates, posts, outcomes, today, filters=filters)


# ---------- routes ----------------------------------------------------------


@router.get("", response_model=ShipperListOut)
async def list_shippers(
    request: Request,
    state: str | None = Query(None, min_length=2, max_length=2),
    source: Literal["FMCSA", "OSM", "Gemini", "Both"] | None = Query(None),
    min_score: int | None = Query(None, ge=0, le=100),
    promoted: bool | None = Query(None),
    q: str | None = Query(None, max_length=128),
    cursor: str | None = Query(None, max_length=256),
    limit: int = Query(50, ge=25, le=100),
) -> ShipperListOut:
    rows = await _rank_all(
        request.app.state.sessionmaker,
        state=state,
        source=source,
        min_score=min_score,
        promoted_only=promoted,
        q=q,
    )
    if cursor:
        rows = _after_cursor(rows, _decode_cursor(cursor))

    page = rows[:limit]
    next_cursor: str | None = None
    if len(rows) > limit and page:
        last = page[-1]
        next_cursor = _encode_cursor(last.score, last.id)

    # Fold in fit_score/fit_reasons — one small lookup per page.
    fit_by_id: dict[str, tuple[int | None, list[str]]] = {}
    ids = [r.id for r in page]
    if ids:
        async with request.app.state.sessionmaker() as s:
            fit_rows = (
                await s.execute(
                    select(ShipperCandidate.id, ShipperCandidate.fit_score, ShipperCandidate.fit_reasons).where(
                        ShipperCandidate.id.in_(ids)
                    )
                )
            ).all()
        for cid, fs, fr in fit_rows:
            fit_by_id[cid] = (fs, list(fr or []))

    return ShipperListOut(
        items=[
            _row_out(r, fit_score=fit_by_id.get(r.id, (None, []))[0], fit_reasons=fit_by_id.get(r.id, (None, []))[1])
            for r in page
        ],
        next_cursor=next_cursor,
    )


# ---- promote --------------------------------------------------------------


def _mint_lead_id(c: ShipperCandidate) -> str:
    """Prefix-encoded id, mirroring `app/sources/fmcsa.py` + `gemini_search.py`.

    Order matches the dedupe ladder: MC → DOT → DOMAIN → SHIPPER-<uuid>. The
    fallback is only reached when a candidate has neither authority nor a
    domain (OSM-only rows) — those still deserve a promotable lead, and a
    random suffix keeps the PK unique without leaking anything.
    """
    mc = c.fmcsa_mc or c.mc
    dot = c.fmcsa_dot or c.dot
    if mc:
        return f"MC-{mc}"
    if dot:
        return f"DOT-{dot}"
    if c.domain:
        return f"DOMAIN-{c.domain}"
    return f"SHIPPER-{uuid.uuid4().hex[:20]}"


async def _find_existing_lead(session, c: ShipperCandidate) -> Lead | None:
    """Dedupe ladder: MC → DOT → domain → case-insensitive (name, state).

    Each rung is one indexed lookup. First hit wins; the caller links to it
    rather than inserting. Ambiguity (two matches at different rungs) is
    resolved by the ladder order — MC beats DOT beats domain beats name+state.
    """
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
        row = (await session.execute(select(Lead).where(Lead.domain == c.domain))).scalar_one_or_none()
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


@router.post("/promote", response_model=PromoteOut)
async def promote(request: Request, body: PromoteIn) -> PromoteOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:  # noqa: SIM117 — inner `s.begin()` needs the bound session
        # ONE transaction: two tables, no half-writes (plan §"Promote = DB transaction").
        async with s.begin():
            c = (
                await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == body.candidate_id))
            ).scalar_one_or_none()
            if c is None:
                raise HTTPException(404, "shipper candidate not found")

            # Idempotent short-circuit — a candidate already pointing at a lead
            # returns the existing link, never a new lead.
            if c.promoted_lead_id:
                # Still copy any enrichment_candidates onto the linked lead (idempotent).
                from app.pipeline.enrichment import copy_enrichment_candidates_to_lead

                await copy_enrichment_candidates_to_lead(s, candidate_id=c.id, lead_id=c.promoted_lead_id, run_id=None)
                return PromoteOut(lead_id=c.promoted_lead_id, created=False)

            existing = await _find_existing_lead(s, c)
            if existing is not None:
                c.promoted_lead_id = existing.id
                from app.pipeline.enrichment import copy_enrichment_candidates_to_lead

                await copy_enrichment_candidates_to_lead(s, candidate_id=c.id, lead_id=existing.id, run_id=None)
                return PromoteOut(lead_id=existing.id, created=False)

            lead_id = _mint_lead_id(c)
            # Defensive: the mint could collide on a re-promote race — treat a
            # concurrent id hit as an idempotent link (created=false).
            already = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
            if already is not None:
                c.promoted_lead_id = already.id
                return PromoteOut(lead_id=already.id, created=False)

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

            await copy_enrichment_candidates_to_lead(s, candidate_id=c.id, lead_id=lead_id, run_id=None)
            return PromoteOut(lead_id=lead_id, created=True)


# ---- detail --------------------------------------------------------------


@router.get("/{candidate_id}", response_model=ShipperDetailOut)
async def get_shipper(request: Request, candidate_id: str) -> ShipperDetailOut:
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as s:
        c = (await s.execute(select(ShipperCandidate).where(ShipperCandidate.id == candidate_id))).scalar_one_or_none()
        if c is None:
            raise HTTPException(404, "shipper candidate not found")

        posts = (await s.execute(select(CapacityPost).where(CapacityPost.status == "open"))).scalars().all()
        outcomes = (await s.execute(select(CallOutcome))).scalars().all()

        promoted_lead: Lead | None = None
        if c.promoted_lead_id:
            promoted_lead = (await s.execute(select(Lead).where(Lead.id == c.promoted_lead_id))).scalar_one_or_none()

    # Run the row through the ranker so the detail column shows the same score
    # + reason chips the list does. Bypass the ranker's state/name drops by
    # trusting the fact that the row already sits in the DB — if a drop rule
    # excludes it, we return the raw fields with score=0 so the operator can
    # still see the row's evidence.
    today = datetime.now(UTC).date()
    ranked = rank_shippers(
        [c],
        posts,
        outcomes,
        today,
        filters=ShipperFilters(limit=SHIPPER_FINDER_LIMIT_CAP),
    )
    if ranked:
        row_out = _row_out(ranked[0], fit_score=c.fit_score, fit_reasons=list(c.fit_reasons or []))
    else:
        row_out = ShipperRowOut(
            id=c.id,
            name=c.name or "",
            state=(c.state or "").upper(),
            city=c.city,
            address=c.address,
            lat=c.lat,
            lng=c.lng,
            sources=list(c.sources or []),
            mc=c.mc,
            dot=c.dot,
            domain=c.domain,
            phone=c.phone,
            primary_email=c.primary_email,
            score=0,
            reasons=[],
            promoted_lead_id=c.promoted_lead_id,
            match_reason=c.match_reason,
            fit_score=c.fit_score,
            fit_reasons=list(c.fit_reasons or []),
        )

    promoted_out: PromotedLeadOut | None = None
    if promoted_lead is not None:
        promoted_out = PromotedLeadOut(
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

    return ShipperDetailOut(
        row=row_out,
        evidence=c.evidence,
        osm_tags=c.osm_tags,
        raw=c.raw,
        osm_ref=c.osm_ref,
        fmcsa_mc=c.fmcsa_mc,
        fmcsa_dot=c.fmcsa_dot,
        first_seen_at=_iso(c.first_seen_at),
        last_seen_at=_iso(c.last_seen_at),
        promoted_lead=promoted_out,
    )
