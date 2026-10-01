"""AI usage + feature-matrix service — DB reads behind ``/ai/*``.

The router (``app/api/ai.py``) owns HTTP: query parsing, pydantic projection.
This service owns the DB reads and the pure aggregation. No FastAPI here.

Returns plain dataclasses so cron jobs / future queue workers can reuse it
without pulling in a Request.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.integrations.adapters.ai.provider import ALLOWED_MODELS, DEFAULT_FEATURES
from app.models import AiUsageLog, SettingsRow


@dataclass
class FeatureChoiceRow:
    provider: str
    model: str


@dataclass
class FeaturesResult:
    features: dict[str, FeatureChoiceRow]
    allowed_models: dict[str, list[str]]
    keys_present: dict[str, bool]


@dataclass
class UsageRowData:
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


@dataclass
class UsageTotalRow:
    calls: int
    tokens: int
    cost_usd: str


@dataclass
class UsageResult:
    since: str
    totals_by_provider: dict[str, UsageTotalRow]
    rows: list[UsageRowData] = field(default_factory=list)


async def _load_ai_features_override(session: AsyncSession) -> dict | None:
    """Resilient read: a missing settings row (fixtures without migrations) falls
    back to the code default. Never raises."""
    try:
        row = (
            await session.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        return (row.ai_features if row else None) or None
    except Exception:  # noqa: BLE001
        return None


async def get_features(session: AsyncSession, settings: Any) -> FeaturesResult:
    """Current per-feature matrix + allowed models + which API keys are set."""
    overrides = await _load_ai_features_override(session) or {}

    out: dict[str, FeatureChoiceRow] = {}
    for feature, default in DEFAULT_FEATURES.items():
        choice = overrides.get(feature) if isinstance(overrides.get(feature), dict) else None
        provider = (choice or {}).get("provider") or default["provider"]
        model = (choice or {}).get("model") or default["model"]
        if provider not in ALLOWED_MODELS or model not in ALLOWED_MODELS.get(provider, []):
            provider, model = default["provider"], default["model"]
        out[feature] = FeatureChoiceRow(provider=provider, model=model)

    gemini_present = bool(
        getattr(settings, "gemini_api_key", None) and settings.gemini_api_key.get_secret_value()
    )
    claude_present = bool(
        getattr(settings, "anthropic_api_key", None)
        and settings.anthropic_api_key.get_secret_value()
    )

    return FeaturesResult(
        features=out,
        allowed_models={k: list(v) for k, v in ALLOWED_MODELS.items()},
        keys_present={"gemini": gemini_present, "claude": claude_present},
    )


def parse_since(since: str) -> datetime:
    """Simple parser: `24h`, `7d`, or an ISO timestamp. Default to 24h window."""
    now = datetime.now(UTC)
    try:
        s = since.strip().lower()
        if s.endswith("h"):
            return now - timedelta(hours=int(s[:-1]))
        if s.endswith("d"):
            return now - timedelta(days=int(s[:-1]))
        parsed = datetime.fromisoformat(s)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed
    except Exception:  # noqa: BLE001
        return now - timedelta(hours=24)


def _ts(dt: Any) -> str:
    try:
        return dt.isoformat() if dt else ""
    except Exception:  # noqa: BLE001
        return ""


async def get_usage(
    session: AsyncSession,
    *,
    since: str = "24h",
    feature: str | None = None,
    provider: str | None = None,
    limit: int = 20,
) -> UsageResult:
    """Totals-by-provider over the full window + latest ``limit`` rows."""
    since_dt = parse_since(since)
    since_naive = since_dt.astimezone(UTC).replace(tzinfo=None)

    conds = [AiUsageLog.created_at >= since_naive]
    if feature:
        conds.append(AiUsageLog.feature == feature)
    if provider:
        conds.append(AiUsageLog.provider == provider)

    rows = (
        (
            await session.execute(
                select(AiUsageLog)
                .where(*conds)
                .order_by(desc(AiUsageLog.created_at))
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    all_rows = (
        (
            await session.execute(
                select(AiUsageLog).where(*conds).order_by(desc(AiUsageLog.created_at))
            )
        )
        .scalars()
        .all()
    )

    totals: dict[str, UsageTotalRow] = {}
    agg: dict[str, dict[str, Decimal | int]] = {}
    for r in all_rows:
        bucket = agg.setdefault(r.provider, {"calls": 0, "tokens": 0, "cost_usd": Decimal(0)})
        bucket["calls"] = int(bucket["calls"]) + 1
        bucket["tokens"] = (
            int(bucket["tokens"]) + int(r.input_tokens or 0) + int(r.output_tokens or 0)
        )
        bucket["cost_usd"] = Decimal(bucket["cost_usd"]) + (
            Decimal(r.cost_usd) if r.cost_usd is not None else Decimal(0)
        )
    for p, b in agg.items():
        totals[p] = UsageTotalRow(
            calls=int(b["calls"]),
            tokens=int(b["tokens"]),
            cost_usd=str(b["cost_usd"]),
        )

    return UsageResult(
        since=since_dt.isoformat(),
        totals_by_provider=totals,
        rows=[
            UsageRowData(
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
