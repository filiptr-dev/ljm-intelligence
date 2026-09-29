"""Capacity Posts API.

Two post kinds:
 - "truck": empty-truck post (equipment + current location + available date + destinations[])
 - "load":  freight we have (origin → destination + pickup date + weight + rate)

Both persisted in `capacity_posts`. Suggestions endpoint ranks crawled brokers/shippers
against a post using the deterministic scorer in `app.pipeline.match`.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.models import CapacityPost, Lead
from app.pipeline.match import score_broker_for_post

router = APIRouter(prefix="/capacity", tags=["capacity"])


class PostIn(BaseModel):
    kind: str = Field(pattern=r"^(truck|load)$")
    equipment: str
    origin_city: str | None = None
    origin_state: str = Field(min_length=2, max_length=2)
    destinations: list[str] = []
    available_date: str | None = None
    dest_city: str | None = None
    dest_state: str | None = None
    pickup_date: str | None = None
    weight_lbs: int | None = None
    rate_usd: int | None = None
    notes: str | None = None


class PostOut(BaseModel):
    id: str
    kind: str
    equipment: str
    origin_city: str | None
    origin_state: str
    destinations: list[str]
    available_date: str | None
    dest_city: str | None
    dest_state: str | None
    pickup_date: str | None
    weight_lbs: int | None
    rate_usd: int | None
    notes: str | None
    status: str
    created_at: str


class PostList(BaseModel):
    items: list[PostOut]


def _serialize(p: CapacityPost) -> PostOut:
    return PostOut(
        id=p.id,
        kind=p.kind,
        equipment=p.equipment,
        origin_city=p.origin_city,
        origin_state=p.origin_state,
        destinations=list(p.destinations or []),
        available_date=p.available_date,
        dest_city=p.dest_city,
        dest_state=p.dest_state,
        pickup_date=p.pickup_date,
        weight_lbs=p.weight_lbs,
        rate_usd=p.rate_usd,
        notes=p.notes,
        status=p.status,
        created_at=p.created_at.isoformat() if p.created_at else "",
    )


@router.get("/posts", response_model=PostList)
async def list_posts(request: Request, kind: str | None = None, limit: int = Query(50, ge=1, le=200)) -> PostList:
    async with request.app.state.sessionmaker() as s:
        q = select(CapacityPost).order_by(desc(CapacityPost.created_at)).limit(limit)
        if kind:
            q = q.where(CapacityPost.kind == kind)
        rows = (await s.execute(q)).scalars().all()
    return PostList(items=[_serialize(p) for p in rows])


@router.post("/posts", response_model=PostOut)
async def create_post(request: Request, body: PostIn) -> PostOut:
    pid = f"CP-{uuid.uuid4().hex[:12]}"
    row = CapacityPost(
        id=pid,
        kind=body.kind,
        equipment=body.equipment,
        origin_city=body.origin_city,
        origin_state=body.origin_state.upper(),
        destinations=[d.upper() for d in body.destinations],
        available_date=body.available_date,
        dest_city=body.dest_city,
        dest_state=body.dest_state.upper() if body.dest_state else None,
        pickup_date=body.pickup_date,
        weight_lbs=body.weight_lbs,
        rate_usd=body.rate_usd,
        notes=body.notes,
    )
    async with request.app.state.sessionmaker() as s:
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return _serialize(row)


class SuggestionOut(BaseModel):
    lead_id: str
    name: str
    state: str
    email: str | None
    phone: str | None
    score: int
    reason: str


class SuggestionList(BaseModel):
    post: PostOut
    items: list[SuggestionOut]


@router.get("/posts/{post_id}/suggestions", response_model=SuggestionList)
async def suggestions(request: Request, post_id: str, top: int = Query(20, ge=1, le=100)) -> SuggestionList:
    async with request.app.state.sessionmaker() as s:
        post = (await s.execute(select(CapacityPost).where(CapacityPost.id == post_id))).scalar_one_or_none()
        if not post:
            raise HTTPException(404, "post not found")
        # Candidate leads: prefer those in the origin state or destination state(s), then broader.
        candidate_states = {post.origin_state}
        if post.dest_state:
            candidate_states.add(post.dest_state)
        candidate_states.update(list(post.destinations or []))

        q = select(Lead).where(Lead.kind.in_(["Broker", "Shipper", "Forwarder"]))
        # Prefer targeted candidates but don't hard-exclude — we still score globally
        rows = (await s.execute(q.limit(500))).scalars().all()

    scored = [score_broker_for_post(post, l) for l in rows]
    scored.sort(key=lambda x: x.score, reverse=True)
    top_items = scored[:top]
    return SuggestionList(
        post=_serialize(post),
        items=[
            SuggestionOut(
                lead_id=s.lead_id,
                name=s.name,
                state=s.state,
                email=s.email,
                phone=s.phone,
                score=s.score,
                reason=s.reason,
            )
            for s in top_items
        ],
    )
