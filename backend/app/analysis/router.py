"""analysis router — HTTP surface for predictions + further analyses.

All endpoints are read-only. Writes happen only in the nightly job
``analysis.nightly`` (see ``app.analysis.jobs``). The deliberate split keeps
dashboard loads fast and the Gemini bill bounded by data volume rather
than page views.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from app.analysis import kpi_service as kpi
from app.analysis import service as svc
from app.db import Session
from app.shared.tenant import current_tenant

router = APIRouter(prefix="/analysis", tags=["analysis"])


class BrokerPredictionOut(BaseModel):
    broker_domain: str
    broker_name: str | None
    win_probability: float
    health_score: int
    best_send_hour: int | None
    is_slow_payer: bool
    churn_risk: float
    reply_speed_lift: float
    first_touch_latency_days: float | None
    computed_at: datetime


class LanePredictionOut(BaseModel):
    origin: str
    dest: str
    equipment: str | None
    price_p50: float | None
    price_p75: float | None
    price_p90: float | None
    season_hint: dict
    sample_size: int
    computed_at: datetime


class LookalikeOut(BaseModel):
    broker_domain: str
    peer_domain: str
    score: float


class ObjectionOut(BaseModel):
    broker_domain: str
    label: str
    count: int
    exemplar: str | None


class WorkloadCellOut(BaseModel):
    day: int
    hour: int
    count: int


class ThreadAgeOut(BaseModel):
    intent: str
    median_minutes: int
    p90_minutes: int
    count: int


class LossReasonOut(BaseModel):
    reason: str
    count: int


class FirstTouchOut(BaseModel):
    broker_domain: str
    latency_days: float | None


class PredictionsPageOut(BaseModel):
    brokers: list[BrokerPredictionOut]
    lanes: list[LanePredictionOut]
    objections: list[ObjectionOut]
    workload: list[WorkloadCellOut]
    thread_age: list[ThreadAgeOut]
    loss_reasons: list[LossReasonOut]
    first_touch: list[FirstTouchOut]


@router.get("/predictions", response_model=PredictionsPageOut)
async def predictions_endpoint(session: Session) -> PredictionsPageOut:
    brokers = await svc.list_broker_predictions(session, limit=200)
    lanes = await svc.list_lane_predictions(session, limit=200)
    objs = await svc.list_objections(session, limit=100)
    workload = await svc.workload_heatmap(session)
    thread_age = await svc.thread_age_by_intent(session)
    losses = await svc.loss_reasons(session)
    first_touch = await svc.first_touch_latency(session)
    return PredictionsPageOut(
        brokers=[BrokerPredictionOut(**r.__dict__) for r in brokers],
        lanes=[LanePredictionOut(**r.__dict__) for r in lanes],
        objections=[ObjectionOut(**r.__dict__) for r in objs],
        workload=[WorkloadCellOut(**r.__dict__) for r in workload],
        thread_age=[ThreadAgeOut(**r.__dict__) for r in thread_age],
        loss_reasons=[LossReasonOut(**r.__dict__) for r in losses],
        first_touch=[FirstTouchOut(broker_domain=r.broker_domain, latency_days=r.latency_days) for r in first_touch],
    )


@router.get("/broker-predictions", response_model=list[BrokerPredictionOut])
async def broker_predictions_endpoint(
    session: Session,
    broker_domain: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[BrokerPredictionOut]:
    rows = await svc.list_broker_predictions(session, broker_domain=broker_domain, limit=limit)
    return [BrokerPredictionOut(**r.__dict__) for r in rows]


@router.get("/lane-predictions", response_model=list[LanePredictionOut])
async def lane_predictions_endpoint(
    session: Session,
    origin: str | None = Query(default=None, max_length=128),
    dest: str | None = Query(default=None, max_length=128),
    equipment: str | None = Query(default=None, max_length=64),
    limit: int = Query(default=200, ge=1, le=1000),
) -> list[LanePredictionOut]:
    rows = await svc.list_lane_predictions(
        session, origin=origin, dest=dest, equipment=equipment, limit=limit
    )
    return [LanePredictionOut(**r.__dict__) for r in rows]


@router.get("/lookalikes/{broker_domain}", response_model=list[LookalikeOut])
async def lookalikes_endpoint(
    session: Session, broker_domain: str, top_n: int = Query(default=5, ge=1, le=50)
) -> list[LookalikeOut]:
    rows = await svc.list_lookalikes(session, broker_domain=broker_domain, top_n=top_n)
    return [LookalikeOut(**r.__dict__) for r in rows]


@router.get("/objections", response_model=list[ObjectionOut])
async def objections_endpoint(
    session: Session,
    broker_domain: str | None = Query(default=None, max_length=255),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[ObjectionOut]:
    rows = await svc.list_objections(session, broker_domain=broker_domain, limit=limit)
    return [ObjectionOut(**r.__dict__) for r in rows]


@router.get("/workload-heatmap", response_model=list[WorkloadCellOut])
async def workload_heatmap_endpoint(session: Session) -> list[WorkloadCellOut]:
    rows = await svc.workload_heatmap(session)
    return [WorkloadCellOut(**r.__dict__) for r in rows]


@router.get("/thread-age", response_model=list[ThreadAgeOut])
async def thread_age_endpoint(session: Session) -> list[ThreadAgeOut]:
    rows = await svc.thread_age_by_intent(session)
    return [ThreadAgeOut(**r.__dict__) for r in rows]


@router.get("/loss-reasons", response_model=list[LossReasonOut])
async def loss_reasons_endpoint(session: Session) -> list[LossReasonOut]:
    rows = await svc.loss_reasons(session)
    return [LossReasonOut(**r.__dict__) for r in rows]


@router.get("/first-touch", response_model=list[FirstTouchOut])
async def first_touch_endpoint(session: Session) -> list[FirstTouchOut]:
    rows = await svc.first_touch_latency(session)
    return [FirstTouchOut(broker_domain=r.broker_domain, latency_days=r.latency_days) for r in rows]


# ---- shared KPI (foundation) endpoints -------------------------------------
#
# Every endpoint here goes through ``app.analysis.kpi_service``. The service
# applies the ``Period`` VO (ET day-snap, 365d cap) and the 60s in-process
# cache; the router just resolves the window from query args.


class MetricPointOut(BaseModel):
    bucket: str
    key: str
    value: float
    n: int


class KpiBlockOut(BaseModel):
    label: str
    unit: str
    value: float
    prev: float
    delta_pct: float | None
    direction: str
    series: list[MetricPointOut]
    thin: bool


class BreakdownRowOut(BaseModel):
    key: str
    value: float
    n: int
    share: float


class BreakdownOut(BaseModel):
    dimension: str
    rows: list[BreakdownRowOut]


class FunnelStepOut(BaseModel):
    label: str
    count: int
    drop_pct: float | None


class FunnelOut(BaseModel):
    steps: list[FunnelStepOut]


class PeriodOut(BaseModel):
    from_: datetime
    to: datetime
    label: str

    model_config = {"populate_by_name": True}


def _resolve_period(
    period: str, from_: datetime | None, to: datetime | None
) -> kpi.Period:
    if from_ and to:
        return kpi.Period(**{"from": from_, "to": to, "label": "custom"})
    return kpi.period_from_label(period)


class BookedVsRejectedOut(BaseModel):
    series: list[MetricPointOut]
    win_rate: list[MetricPointOut]


class OverviewKpiOut(BaseModel):
    period: dict
    tiles: list[KpiBlockOut]
    booked_vs_rejected: BookedVsRejectedOut


@router.get("/overview", response_model=OverviewKpiOut)
async def overview_kpi_endpoint(
    session: Session,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="7d"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    return await kpi.overview_kpis(session, current_tenant(), p)


class CrawlerKpiOut(BaseModel):
    period: dict
    tiles: list[KpiBlockOut]
    by_state: BreakdownOut
    last_crawl_finished_at: str | None


@router.get("/crawler", response_model=CrawlerKpiOut)
async def crawler_kpi_endpoint(
    session: Session,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="7d"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    return await kpi.crawler_kpis(session, current_tenant(), p)


class CallOutcomeKpiOut(BaseModel):
    period: dict
    tiles: list[KpiBlockOut]
    mix: BreakdownOut
    trend: list[MetricPointOut]


@router.get("/call-outcomes", response_model=CallOutcomeKpiOut)
async def call_outcome_kpi_endpoint(
    session: Session,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="7d"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    return await kpi.call_outcome_kpis(session, current_tenant(), p)


class DayPulseOut(BaseModel):
    today_et: str
    today: dict
    yesterday: dict
    conversion_today: float
    conversion_yesterday: float


@router.get("/day-pulse", response_model=DayPulseOut)
async def day_pulse_endpoint(session: Session) -> Any:
    return await kpi.day_pulse(session, current_tenant())


class TopbarCountersOut(BaseModel):
    today_et: str
    scanned: int
    found: int
    sent: int
    replies: int
    # Pill carries the real crawler state so the top-bar pill and the counters
    # stay in lockstep off one poll — see plan 2026-10-06.
    last_crawl_at: str | None
    last_crawl_status: Literal["none", "running", "idle_recent", "idle_stale", "error"]


@router.get("/topbar-counters", response_model=TopbarCountersOut)
async def topbar_counters_endpoint(session: Session) -> Any:
    """Top-bar "today" counters (ET, tenant) — replaces the client-side baseline."""
    return await kpi.topbar_counters(session, current_tenant())


class LiveFeedItemOut(BaseModel):
    id: str
    at: datetime
    kind: Literal["found", "outreach", "reply"]
    text: str
    detail: str | None = None


class LiveFeedOut(BaseModel):
    items: list[LiveFeedItemOut]


@router.get("/live-feed", response_model=LiveFeedOut)
async def live_feed_endpoint(
    session: Session,
    limit: int = Query(default=25, ge=1, le=100),
) -> Any:
    """Last 12h of real activity for the live-feed widgets.

    Three real kinds only — ``found`` / ``outreach`` / ``reply``. Rolling
    12h window; tenant-scoped on every leg (fail closed).
    """
    return await kpi.live_feed(session, current_tenant(), limit=limit)


class CampaignStatusItemOut(BaseModel):
    email: str
    sent_at: str | None
    replied_at: str | None


class CampaignStatusOut(BaseModel):
    items: list[CampaignStatusItemOut]


class CampaignStatusIn(BaseModel):
    created_at: datetime
    emails: list[str]


@router.post("/campaign-status", response_model=CampaignStatusOut)
async def campaign_status_endpoint(session: Session, body: CampaignStatusIn) -> Any:
    """Real per-recipient sent/replied timestamps for a client-side campaign.

    Campaigns live in localStorage in v1 (no backend table). The dashboard
    posts the campaign's ``created_at`` + the list of recipient emails and
    gets back the real ``sent_at`` (latest ``SentLog`` in-window) and
    ``replied_at`` (earliest inbound mail in-window) for each. Opens and
    wins are **not** returned — no table backs them.

    POST (not GET) because the recipient list can be large and GET query
    strings have host-dependent limits; see plan 2026-10-06 "Honest
    campaign metrics" — the plan's `GET /campaigns/{client_id}/status`
    sketch is realised here as a tenant-scoped analysis read because
    there is no backend Campaign table to key off of.
    """
    return await kpi.campaign_status(
        session, current_tenant(), body.created_at, body.emails
    )


class LaneRow(BaseModel):
    lane: str
    loads: int
    avg_rate_usd: float | None
    avg_usd_per_mile: float | None
    n: int


class LaneKpiOut(BaseModel):
    period: dict
    rows: list[LaneRow]
    top: BreakdownOut


@router.get("/lanes", response_model=LaneKpiOut)
async def lane_kpi_endpoint(
    session: Session,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="90d"),
    region: str | None = Query(default=None, max_length=4),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    return await kpi.lane_performance(session, current_tenant(), p, region)


class CapacityKpiOut(BaseModel):
    period: dict
    tiles: list[KpiBlockOut]
    funnel: FunnelOut


@router.get("/capacity", response_model=CapacityKpiOut)
async def capacity_kpi_endpoint(
    session: Session,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="30d"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    return await kpi.capacity_kpis(session, current_tenant(), p)


class BrokerKpiOut(BaseModel):
    broker_id: str
    period: dict
    tiles: list[KpiBlockOut]
    thin: bool


@router.get("/broker-kpis/{broker_id}", response_model=BrokerKpiOut)
async def broker_kpi_endpoint(
    session: Session,
    broker_id: str,
    period: Literal["today", "7d", "30d", "90d", "custom"] = Query(default="30d"),
    from_: datetime | None = Query(default=None, alias="from"),
    to: datetime | None = Query(default=None),
) -> Any:
    p = _resolve_period(period, from_, to)
    out = await kpi.broker_kpis(session, current_tenant(), broker_id, p)
    if out.get("error") == "not_found":
        raise HTTPException(status_code=404, detail="broker_not_found")
    return out
