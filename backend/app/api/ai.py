"""AI usage + feature-matrix routes — thin router over ``app.analysis.ai_service``.

Two owner-only reads behind the shared `current_user` dep:

  GET  /ai/features          — current per-feature matrix + allowed models + key flags.
  GET  /ai/usage?since=24h   — totals-by-provider + last N rows, powers the cost strip.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel

from app.analysis.ai_service import (
    get_features as svc_get_features,
    get_usage as svc_get_usage,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/ai", tags=["ai"])


class FeatureChoice(BaseModel):
    provider: str
    model: str


class FeaturesResponse(BaseModel):
    features: dict[str, FeatureChoice]
    allowed_models: dict[str, list[str]]
    keys_present: dict[str, bool]


class UsageRow(BaseModel):
    id: int
    feature: str
    provider: str
    model: str
    status: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    cost_usd: str
    error: str | None
    compare_id: str | None
    created_at: str


class UsageTotal(BaseModel):
    calls: int
    tokens: int
    cost_usd: str


class UsageResponse(BaseModel):
    since: str
    totals_by_provider: dict[str, UsageTotal]
    rows: list[UsageRow]


@router.get("/features", response_model=FeaturesResponse)
async def get_features(request: Request) -> FeaturesResponse:
    async with request.app.state.sessionmaker() as s:
        result = await svc_get_features(s, request.app.state.settings)
    return FeaturesResponse(
        features={k: FeatureChoice(**v.__dict__) for k, v in result.features.items()},
        allowed_models=result.allowed_models,
        keys_present=result.keys_present,
    )


@router.get("/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    since: str = Query(default="24h", max_length=32),
    feature: str | None = Query(default=None, max_length=48),
    provider: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=20, ge=1, le=200),
) -> UsageResponse:
    async with request.app.state.sessionmaker() as s:
        result = await svc_get_usage(
            s, since=since, feature=feature, provider=provider, limit=limit
        )
    return UsageResponse(
        since=result.since,
        totals_by_provider={
            k: UsageTotal(**v.__dict__) for k, v in result.totals_by_provider.items()
        },
        rows=[UsageRow(**r.__dict__) for r in result.rows],
    )
