"""Lanes history HTTP surface — thin: parse query, call the services.

GET  /analysis/lanes/summary   KPIs + stacked cost series + length bands + plain-language statements
GET  /analysis/lanes/top       top lanes with trend (state or city level)
GET  /analysis/lanes/heatmap   points + arcs + per-state/city/lane entities for the map popover
GET  /analysis/lanes/runs      keyset-paginated runs table
GET  /analysis/lanes/ai        cached per-entity AI suggestions only (no model call)
POST /analysis/lanes/ai        AI insights (cached 24h; ``refresh=true`` forces a new call)
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Query, Request

from app.analysis import lanes_ai_service as ai, lanes_history_service as svc
from app.analysis.schemas import HeatmapData, LaneAiInsights, LanesPeriod, LanesSummary, RunsPage, TopLanes
from app.db import Session
from app.shared.tenant import current_tenant

router = APIRouter(prefix="/analysis/lanes", tags=["analysis"])


def _scope(state: str | None, lane: str | None) -> svc.Scope:
    return svc.Scope(
        tenant_id=current_tenant(),
        state=state.strip().upper()[:8] if state else None,
        lane=lane.strip()[:300] if lane else None,
    )


_State = Query(default=None, max_length=8, description="Filter: runs touching this state")
_Lane = Query(default=None, max_length=300, description='Filter: one lane, "Chicago,IL>Atlanta,GA" or "IL>GA"')


@router.get("/summary", response_model=LanesSummary)
async def lanes_summary(
    session: Session, period: LanesPeriod = "month", state: str | None = _State, lane: str | None = _Lane
) -> LanesSummary:
    return await svc.summary(session, _scope(state, lane), period)


@router.get("/top", response_model=TopLanes)
async def lanes_top(
    session: Session,
    period: LanesPeriod = "month",
    level: Literal["state", "city"] = "state",
    limit: int = Query(default=20, ge=1, le=50),
    state: str | None = _State,
    lane: str | None = _Lane,
) -> TopLanes:
    return await svc.top_lanes(session, _scope(state, lane), period, level, limit)


@router.get("/heatmap", response_model=HeatmapData)
async def lanes_heatmap(
    session: Session, period: LanesPeriod = "month", state: str | None = _State, lane: str | None = _Lane
) -> HeatmapData:
    return await svc.heatmap(session, _scope(state, lane), period)


@router.get("/runs", response_model=RunsPage)
async def lanes_runs(
    session: Session,
    period: LanesPeriod = "month",
    cursor: str | None = Query(default=None, max_length=200),
    limit: int = Query(default=50, ge=1, le=200),
    state: str | None = _State,
    lane: str | None = _Lane,
) -> RunsPage:
    return await svc.runs_page(session, _scope(state, lane), period, cursor, limit)


@router.get("/ai", response_model=dict[str, list[str]])
async def lanes_ai_cached_entities(
    session: Session, period: LanesPeriod = "month", state: str | None = _State, lane: str | None = _Lane
) -> dict[str, list[str]]:
    """Whatever per-entity suggestions are cached. Never calls the model."""
    return await ai.get_cached_entity_insights(session, _scope(state, lane), period)


@router.post("/ai", response_model=LaneAiInsights)
async def lanes_ai(
    request: Request,
    session: Session,
    period: LanesPeriod = "month",
    refresh: bool = False,
    state: str | None = _State,
    lane: str | None = _Lane,
) -> LaneAiInsights:
    from app.integrations.adapters.ai import provider as ai_provider

    provider = None
    resolve_error: str | None = None
    try:
        provider = ai_provider.get_for("inbox_analysis", settings=request.app.state.settings)
    except Exception as exc:  # noqa: BLE001
        resolve_error = f"resolve_failed:{type(exc).__name__}"
    return await ai.get_lane_insights(
        session, _scope(state, lane), period, provider=provider, resolve_error=resolve_error, refresh=refresh
    )
