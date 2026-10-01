"""AI usage + feature-matrix routes.

Two owner-only reads behind the shared `current_user` dep:

  GET  /ai/features          — current per-feature matrix + allowed models + key flags.
  GET  /ai/usage?since=24h   — totals-by-provider + last N rows, powers the cost strip.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel
from sqlalchemy import desc, select

from app.models import AiUsageLog, SettingsRow
from app.integrations.adapters.ai.provider import (
    ALLOWED_MODELS,
    DEFAULT_FEATURES,
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


# --- helpers ---------------------------------------------------------------


async def _load_ai_features(request: Request) -> dict | None:
    # Resilient: a missing settings table (test fixtures without migrations)
    # falls back to the code default. The matrix is still rendered.
    try:
        async with request.app.state.sessionmaker() as s:
            row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
            return (row.ai_features if row else None) or None
    except Exception as exc:  # noqa: BLE001
        log.info("/ai: settings lookup skipped: %s", exc)
        return None


# --- /ai/features -----------------------------------------------------------


@router.get("/features", response_model=FeaturesResponse)
async def get_features(request: Request) -> FeaturesResponse:
    settings = request.app.state.settings
    overrides = await _load_ai_features(request) or {}

    out: dict[str, FeatureChoice] = {}
    for feature, default in DEFAULT_FEATURES.items():
        choice = overrides.get(feature) if isinstance(overrides.get(feature), dict) else None
        provider = (choice or {}).get("provider") or default["provider"]
        model = (choice or {}).get("model") or default["model"]
        if provider not in ALLOWED_MODELS or model not in ALLOWED_MODELS.get(provider, []):
            provider, model = default["provider"], default["model"]
        out[feature] = FeatureChoice(provider=provider, model=model)

    gemini_present = bool(getattr(settings, "gemini_api_key", None) and settings.gemini_api_key.get_secret_value())
    claude_present = bool(
        getattr(settings, "anthropic_api_key", None) and settings.anthropic_api_key.get_secret_value()
    )

    return FeaturesResponse(
        features=out,
        allowed_models={k: list(v) for k, v in ALLOWED_MODELS.items()},
        keys_present={"gemini": gemini_present, "claude": claude_present},
    )


# --- /ai/usage --------------------------------------------------------------


def _parse_since(since: str) -> datetime:
    # Simple parser: `24h`, `7d`, or an ISO timestamp. Default to 24h window.
    now = datetime.now(UTC)
    try:
        s = since.strip().lower()
        if s.endswith("h"):
            return now - timedelta(hours=int(s[:-1]))
        if s.endswith("d"):
            return now - timedelta(days=int(s[:-1]))
        # Fallback: assume ISO. Reject naive / bad input by returning 24h.
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except Exception:  # noqa: BLE001
        return now - timedelta(hours=24)


@router.get("/usage", response_model=UsageResponse)
async def usage(
    request: Request,
    since: str = Query(default="24h", max_length=32),
    feature: str | None = Query(default=None, max_length=48),
    provider: str | None = Query(default=None, max_length=16),
    limit: int = Query(default=20, ge=1, le=200),
) -> UsageResponse:
    since_dt = _parse_since(since)
    # Compare SQLAlchemy columns against a naive timestamp so SQLite (which
    # stores DateTime naive-UTC) and Postgres (timestamp with tz) both agree.
    since_naive = since_dt.astimezone(UTC).replace(tzinfo=None)

    conds = [AiUsageLog.created_at >= since_naive]
    if feature:
        conds.append(AiUsageLog.feature == feature)
    if provider:
        conds.append(AiUsageLog.provider == provider)

    async with request.app.state.sessionmaker() as s:
        rows = (
            (await s.execute(select(AiUsageLog).where(*conds).order_by(desc(AiUsageLog.created_at)).limit(limit)))
            .scalars()
            .all()
        )
        # Totals pulled from the same window WITHOUT the row-limit — the strip
        # should reflect the whole window, not just the latest 20 rows.
        all_rows = (
            (await s.execute(select(AiUsageLog).where(*conds).order_by(desc(AiUsageLog.created_at)))).scalars().all()
        )

    totals: dict[str, UsageTotal] = {}
    agg: dict[str, dict[str, Decimal | int]] = {}
    for r in all_rows:
        bucket = agg.setdefault(r.provider, {"calls": 0, "tokens": 0, "cost_usd": Decimal(0)})
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["tokens"] = int(bucket["tokens"]) + int(r.input_tokens or 0) + int(r.output_tokens or 0)
        bucket["cost_usd"] = Decimal(bucket["cost_usd"]) + (
            Decimal(r.cost_usd) if r.cost_usd is not None else Decimal(0)
        )
    for p, b in agg.items():
        totals[p] = UsageTotal(calls=int(b["calls"]), tokens=int(b["tokens"]), cost_usd=str(b["cost_usd"]))

    def _ts(dt) -> str:
        try:
            return dt.isoformat() if dt else ""
        except Exception:  # noqa: BLE001
            return ""

    return UsageResponse(
        since=since_dt.isoformat(),
        totals_by_provider=totals,
        rows=[
            UsageRow(
                id=r.id,
                feature=r.feature,
                provider=r.provider,
                model=r.model,
                status=r.status,
                input_tokens=int(r.input_tokens or 0),
                output_tokens=int(r.output_tokens or 0),
                latency_ms=int(r.latency_ms or 0),
                cost_usd=str(r.cost_usd if r.cost_usd is not None else Decimal(0)),
                error=r.error,
                compare_id=r.compare_id,
                created_at=_ts(r.created_at),
            )
            for r in rows
        ],
    )
