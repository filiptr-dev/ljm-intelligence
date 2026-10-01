"""Overview — thin router over ``app.analysis.overview_service``.

One route, one aggregate read:

  GET /overview/today  → OverviewTodayOut

Business logic (ranked call list, new-leads-since-last-crawl, booked-vs-rejected
series, deterministic do_next merge) lives in the service.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel

from app.analysis.overview_service import get_today as svc_get_today

router = APIRouter(prefix="/overview", tags=["overview"])


# ---------- schemas ---------------------------------------------------------


class TileCount(BaseModel):
    value: int
    href: str


class NewLeadsTile(BaseModel):
    value: int
    since: str | None
    href: str


class LoadsBookedTile(BaseModel):
    value: int
    demo: bool
    href: str


class Tiles(BaseModel):
    to_call_today: TileCount
    new_leads_since_last_crawl: NewLeadsTile
    loads_booked_90d: LoadsBookedTile


class DoNextCall(BaseModel):
    # ``Literal`` on the discriminator so the generated TS schema narrows the
    # ``do_next`` union — ``kind: str`` would erase the type and the frontend
    # could not tell a call row from a capacity match.
    kind: Literal["call"] = "call"
    lead_id: str
    name: str
    state: str
    phone: str
    score: int
    reason: str
    href: str = "/call-list"


class DoNextCapacityMatch(BaseModel):
    kind: Literal["capacity_match"] = "capacity_match"
    post_id: str
    equipment: str
    origin_state: str
    lead_id: str
    lead_name: str
    reason: str
    href: str = "/capacity"


class DoNextNewLead(BaseModel):
    kind: Literal["new_lead"] = "new_lead"
    lead_id: str
    name: str
    state: str
    kind_label: str
    first_seen_at: str
    href: str = "/leads"


class MonthPoint(BaseModel):
    month: str
    booked: int
    rejected: int


class BookedVsRejected(BaseModel):
    demo: bool
    series: list[MonthPoint]


class HeaderActions(BaseModel):
    crawl_now_href: str = "/leads"
    new_post_href: str = "/capacity?new=1"


class OverviewTodayOut(BaseModel):
    date: str
    tiles: Tiles
    do_next: list[DoNextCall | DoNextCapacityMatch | DoNextNewLead]
    booked_vs_rejected: BookedVsRejected
    header: HeaderActions


# ---------- route -----------------------------------------------------------


def _to_do_next(r) -> DoNextCall | DoNextCapacityMatch | DoNextNewLead:
    if r.kind == "call":
        return DoNextCall(
            lead_id=r.lead_id, name=r.name, state=r.state, phone=r.phone,
            score=r.score, reason=r.reason,
        )
    if r.kind == "capacity_match":
        return DoNextCapacityMatch(
            post_id=r.post_id, equipment=r.equipment, origin_state=r.origin_state,
            lead_id=r.lead_id, lead_name=r.lead_name, reason=r.reason,
        )
    return DoNextNewLead(
        lead_id=r.lead_id, name=r.name, state=r.state,
        kind_label=r.kind_label, first_seen_at=r.first_seen_at,
    )


@router.get("/today", response_model=OverviewTodayOut)
async def get_today(request: Request) -> OverviewTodayOut:
    result = await svc_get_today(request.app.state.sessionmaker)
    return OverviewTodayOut(
        date=result.date,
        tiles=Tiles(
            to_call_today=TileCount(**result.tiles.to_call_today.__dict__),
            new_leads_since_last_crawl=NewLeadsTile(
                **result.tiles.new_leads_since_last_crawl.__dict__
            ),
            loads_booked_90d=LoadsBookedTile(**result.tiles.loads_booked_90d.__dict__),
        ),
        do_next=[_to_do_next(r) for r in result.do_next],
        booked_vs_rejected=BookedVsRejected(
            demo=result.booked_vs_rejected.demo,
            series=[MonthPoint(**p.__dict__) for p in result.booked_vs_rejected.series],
        ),
        header=HeaderActions(),
    )
