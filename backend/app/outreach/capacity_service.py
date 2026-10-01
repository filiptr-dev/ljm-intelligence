"""Capacity-posts service — list / create / suggest.

Thin business layer behind ``app/api/capacity.py``. Owns DB reads/writes and the
deterministic match-scoring that produces per-post suggestions.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapacityPost, Lead
from app.pipeline.match import score_broker_for_post


class PostNotFoundError(Exception):
    """Raised when a capacity-post id does not exist."""


@dataclass
class PostRow:
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


@dataclass
class SuggestionRow:
    lead_id: str
    name: str
    state: str
    email: str | None
    phone: str | None
    score: int
    reason: str


@dataclass
class SuggestionsResult:
    post: PostRow
    items: list[SuggestionRow] = field(default_factory=list)


def _row(p: CapacityPost) -> PostRow:
    return PostRow(
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


async def list_posts(
    session: AsyncSession, *, kind: str | None = None, limit: int = 50
) -> list[PostRow]:
    q = select(CapacityPost).order_by(desc(CapacityPost.created_at)).limit(limit)
    if kind:
        q = q.where(CapacityPost.kind == kind)
    rows = (await session.execute(q)).scalars().all()
    return [_row(p) for p in rows]


async def create_post(
    session: AsyncSession,
    *,
    kind: str,
    equipment: str,
    origin_state: str,
    destinations: list[str],
    origin_city: str | None = None,
    available_date: str | None = None,
    dest_city: str | None = None,
    dest_state: str | None = None,
    pickup_date: str | None = None,
    weight_lbs: int | None = None,
    rate_usd: int | None = None,
    notes: str | None = None,
) -> PostRow:
    pid = f"CP-{uuid.uuid4().hex[:12]}"
    row = CapacityPost(
        id=pid,
        kind=kind,
        equipment=equipment,
        origin_city=origin_city,
        origin_state=origin_state.upper(),
        destinations=[d.upper() for d in destinations],
        available_date=available_date,
        dest_city=dest_city,
        dest_state=dest_state.upper() if dest_state else None,
        pickup_date=pickup_date,
        weight_lbs=weight_lbs,
        rate_usd=rate_usd,
        notes=notes,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return _row(row)


async def suggestions_for_post(
    session: AsyncSession, post_id: str, *, top: int = 20
) -> SuggestionsResult:
    post = (
        await session.execute(select(CapacityPost).where(CapacityPost.id == post_id))
    ).scalar_one_or_none()
    if not post:
        raise PostNotFoundError(post_id)

    # Candidate leads: all broker/shipper/forwarder leads at demo scale. The
    # scorer handles targeting; we still rank globally.
    rows = (
        await session.execute(
            select(Lead).where(Lead.kind.in_(["Broker", "Shipper", "Forwarder"])).limit(500)
        )
    ).scalars().all()

    scored = [score_broker_for_post(post, l) for l in rows]
    scored.sort(key=lambda x: x.score, reverse=True)
    items = [
        SuggestionRow(
            lead_id=s.lead_id,
            name=s.name,
            state=s.state,
            email=s.email,
            phone=s.phone,
            score=s.score,
            reason=s.reason,
        )
        for s in scored[:top]
    ]
    return SuggestionsResult(post=_row(post), items=items)
