"""Capacity Posts API — thin router over ``app.outreach.capacity_service``.

Two post kinds:
 - "truck": empty-truck post (equipment + current location + available date + destinations[])
 - "load":  freight we have (origin → destination + pickup date + weight + rate)

Suggestions endpoint ranks crawled brokers/shippers against a post using the
deterministic scorer in `app.pipeline.match` — the service owns the pure logic.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from app.outreach.capacity_service import (
    PostNotFoundError,
    PostRow,
    create_post as svc_create_post,
    list_posts as svc_list_posts,
    suggestions_for_post as svc_suggestions,
)

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


def _out(r: PostRow) -> PostOut:
    return PostOut(**r.__dict__)


@router.get("/posts", response_model=PostList)
async def list_posts(
    request: Request, kind: str | None = None, limit: int = Query(50, ge=1, le=200)
) -> PostList:
    async with request.app.state.sessionmaker() as s:
        rows = await svc_list_posts(s, kind=kind, limit=limit)
    return PostList(items=[_out(r) for r in rows])


@router.post("/posts", response_model=PostOut)
async def create_post(request: Request, body: PostIn) -> PostOut:
    async with request.app.state.sessionmaker() as s:
        row = await svc_create_post(
            s,
            kind=body.kind,
            equipment=body.equipment,
            origin_state=body.origin_state,
            destinations=body.destinations,
            origin_city=body.origin_city,
            available_date=body.available_date,
            dest_city=body.dest_city,
            dest_state=body.dest_state,
            pickup_date=body.pickup_date,
            weight_lbs=body.weight_lbs,
            rate_usd=body.rate_usd,
            notes=body.notes,
        )
    return _out(row)


class SuggestionOut(BaseModel):
    lead_id: str
    name: str
    state: str
    email: str | None
    phone: str | None
    score: int
    reason: str


class ShipperSuggestionOut(BaseModel):
    candidate_id: str
    name: str
    state: str
    city: str | None
    primary_email: str | None
    phone: str | None
    score: int
    reason: str
    promoted_lead_id: str | None


class SuggestionList(BaseModel):
    post: PostOut
    items: list[SuggestionOut]
    shippers: list[ShipperSuggestionOut] = Field(default_factory=list)


@router.get("/posts/{post_id}/suggestions", response_model=SuggestionList)
async def suggestions(
    request: Request, post_id: str, top: int = Query(20, ge=1, le=100)
) -> SuggestionList:
    async with request.app.state.sessionmaker() as s:
        try:
            result = await svc_suggestions(s, post_id, top=top)
        except PostNotFoundError as exc:
            raise HTTPException(404, "post not found") from exc
    return SuggestionList(
        post=_out(result.post),
        items=[SuggestionOut(**r.__dict__) for r in result.items],
        shippers=[ShipperSuggestionOut(**r.__dict__) for r in result.shippers],
    )
