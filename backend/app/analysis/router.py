"""analysis router — HTTP surface for predictions + further analyses.

All endpoints are read-only. Writes happen only in the nightly job
``analysis.nightly`` (see ``app.analysis.jobs``). The deliberate split keeps
dashboard loads fast and the Gemini bill bounded by data volume rather
than page views.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from app.analysis import service as svc
from app.db import Session

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
