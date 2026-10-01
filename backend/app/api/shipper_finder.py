"""Shipper Finder API — thin router over ``app.prospecting.shipper_finder_service``.

Three routes:

  GET  /tools/shipper-finder           — filtered, ranked, cursor-paginated
  POST /tools/shipper-finder/promote   — one-write gate into `leads` (idempotent)
  GET  /tools/shipper-finder/{id}      — single-candidate detail (right column)

Cursor encoding stays in the router — it is HTTP sugar, not business logic.
"""

from __future__ import annotations

import base64
import binascii
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.pipeline.shipper_rank import ShipperRow
from app.prospecting.shipper_finder_service import (
    NotFoundError,
    PromotedLeadRow,
    get_detail as svc_get_detail,
    promote as svc_promote,
    rank_all as svc_rank_all,
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


# ---------- cursor helpers --------------------------------------------------


def _encode_cursor(score: int, id_: str) -> str:
    raw = f"{score}:{id_}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str) -> tuple[int, str]:
    """Decode ``base64(score:id)``. Raises ``HTTPException(400)`` on garbage."""
    try:
        pad = "=" * (-len(cursor) % 4)
        raw = base64.urlsafe_b64decode(cursor + pad).decode("ascii")
        score_s, id_ = raw.split(":", 1)
        return int(score_s), id_
    except (ValueError, binascii.Error, UnicodeDecodeError) as exc:
        raise HTTPException(400, f"invalid cursor: {exc}") from exc


def _after_cursor(rows: list[ShipperRow], cursor: tuple[int, str]) -> list[ShipperRow]:
    cs, cid = cursor
    return [r for r in rows if (r.score < cs) or (r.score == cs and r.id > cid)]


def _after_fit_cursor(
    rows: list[ShipperRow],
    fit_by_id: dict[str, tuple[int | None, list[str]]],
    cursor: tuple[int, str],
) -> list[ShipperRow]:
    cf, cid = cursor
    kept: list[ShipperRow] = []
    for r in rows:
        rf = fit_by_id.get(r.id, (None, []))[0]
        rf_key = rf if rf is not None else -1
        if rf_key < cf or (rf_key == cf and r.id > cid):
            kept.append(r)
    return kept


# ---------- serializers -----------------------------------------------------


def _row_out(
    r: ShipperRow, *, fit_score: int | None = None, fit_reasons: list[str] | None = None
) -> ShipperRowOut:
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
    sort: Literal["lane", "fit"] = Query("lane"),
) -> ShipperListOut:
    result = await svc_rank_all(
        request.app.state.sessionmaker,
        state=state,
        source=source,
        min_score=min_score,
        promoted_only=promoted,
        q=q,
    )
    rows = result.rows
    fit_by_id = result.fit_by_id

    if sort == "fit":
        # ``-1`` sentinel for null so nulls sort last under DESC; final tiebreak
        # on id ASC. Explicit ``is not None`` — ``fs or -1`` would collapse
        # ``fit_score=0`` into the null bucket (0 is falsy in Python), breaking
        # cursor pagination at that boundary.
        def _fit_sort_key(r):
            fs = fit_by_id.get(r.id, (None, []))[0]
            return (-(fs if fs is not None else -1), r.id)

        rows = sorted(rows, key=_fit_sort_key)
        if cursor:
            rows = _after_fit_cursor(rows, fit_by_id, _decode_cursor(cursor))
    else:
        if cursor:
            rows = _after_cursor(rows, _decode_cursor(cursor))

    page = rows[:limit]
    next_cursor: str | None = None
    if len(rows) > limit and page:
        last = page[-1]
        if sort == "fit":
            fs = fit_by_id.get(last.id, (None, []))[0]
            next_cursor = _encode_cursor(fs if fs is not None else -1, last.id)
        else:
            next_cursor = _encode_cursor(last.score, last.id)

    return ShipperListOut(
        items=[
            _row_out(
                r,
                fit_score=fit_by_id.get(r.id, (None, []))[0],
                fit_reasons=fit_by_id.get(r.id, (None, []))[1],
            )
            for r in page
        ],
        next_cursor=next_cursor,
    )


@router.post("/promote", response_model=PromoteOut)
async def promote(request: Request, body: PromoteIn) -> PromoteOut:
    try:
        result = await svc_promote(request.app.state.sessionmaker, body.candidate_id)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return PromoteOut(lead_id=result.lead_id, created=result.created)


def _promoted_out(r: PromotedLeadRow | None) -> PromotedLeadOut | None:
    return PromotedLeadOut(**r.__dict__) if r is not None else None


@router.get("/{candidate_id}", response_model=ShipperDetailOut)
async def get_shipper(request: Request, candidate_id: str) -> ShipperDetailOut:
    try:
        result = await svc_get_detail(request.app.state.sessionmaker, candidate_id)
    except NotFoundError as exc:
        raise HTTPException(404, str(exc)) from exc
    return ShipperDetailOut(
        row=_row_out(result.row, fit_score=result.fit_score, fit_reasons=result.fit_reasons),
        evidence=result.evidence,
        osm_tags=result.osm_tags,
        raw=result.raw,
        osm_ref=result.osm_ref,
        fmcsa_mc=result.fmcsa_mc,
        fmcsa_dot=result.fmcsa_dot,
        first_seen_at=result.first_seen_at,
        last_seen_at=result.last_seen_at,
        promoted_lead=_promoted_out(result.promoted_lead),
    )
