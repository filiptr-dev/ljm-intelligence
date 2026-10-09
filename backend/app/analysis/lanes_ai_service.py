"""AI insights for the lanes page: actionable moves, never a prose summary.

Mirrors ``lead_ai_summary_service``: aggregates only (never raw rows) go
through the untrusted-data fence, the reply must match a strict JSON schema,
only successful output is cached (24h, keyed by an input hash), and every
failure degrades to ``status="unavailable"`` + ``ai_error`` rather than text
the model made up.

Amendment A2: the same call also returns ``entity_insights`` — suggestions
keyed by ``state:TX`` / ``lane:Dallas,TX>Memphis,TN`` — so the map popover can
show AI advice on hover without any model call.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import lanes_history_service as H, lanes_repository as repo
from app.analysis.models import LaneInsightsCache
from app.analysis.schemas import (
    LaneAiInsights,
    LaneInsightItem,
    LanesPeriod,
    LeverItem,
    ShiftItem,
)
from app.shared.untrusted import FENCE_CLOSE, FENCE_OPEN, neutralize as _neutralize, security_instruction

log = logging.getLogger(__name__)

LANES_AI_TIMEOUT_S = 30.0
CACHE_TTL = timedelta(hours=24)
MAX_ITEMS = 4
MAX_PER_ENTITY = 2
TOP_LANES = 12
TOP_STATES = 8


def period_key(period: str, scope: H.Scope) -> str:
    return f"{period}|{scope.state or ''}|{scope.lane or ''}"[:160]


async def build_aggregates(session: AsyncSession, scope: H.Scope, period: LanesPeriod, now: datetime | None) -> dict[str, Any]:
    s = await H.summary(session, scope, period, now)
    lanes = await H.top_lanes(session, scope, period, "city", TOP_LANES, now)
    heat = await H.heatmap(session, scope, period, now)
    san = _neutralize
    return {
        "window": s.window_label,
        "comparison": s.trend.label,
        "kpis": s.kpis.model_dump(),
        "trend": s.trend.model_dump(exclude={"label"}),
        "cost_split_pct": {c.key: c.pct for c in s.cost_split},
        "length_bands_pct": {b.band: b.pct for b in s.length_bands},
        "lanes": [
            {"key": f"lane:{ln.key}", "lane": san(f"{ln.origin} -> {ln.dest}"), "runs": ln.runs,
             "avg_rate_per_mile": ln.avg_rate_per_mi, "margin_pct": ln.margin_pct,
             "rate_trend_pct": ln.trend_pct, "volume_trend_pct": ln.runs_trend_pct}
            for ln in lanes.lanes
        ],
        "states": [
            {"key": e.key, "state": e.label, "runs": e.metrics.runs, "revenue": e.metrics.revenue,
             "avg_rate_per_mile": e.metrics.rate_per_mile, "margin_pct": e.metrics.margin_pct,
             "rate_trend_pct": e.trend.rate_pct, "volume_trend_pct": e.trend.runs_pct}
            for e in heat.states[:TOP_STATES]
        ],
        "market_shift_notes": [x.text for x in s.statements if x.key == "shift"],
    }


def allowed_entity_keys(agg: dict[str, Any]) -> set[str]:
    return {x["key"] for x in agg["lanes"]} | {x["key"] for x in agg["states"]}


def _build_prompt(agg: dict[str, Any]) -> str:
    return (
        "You advise a small US trucking carrier on which freight lanes to run. Using ONLY the aggregated numbers "
        "below, reply with a single JSON object and nothing else, exactly this shape: "
        '{"focus_lanes":[{"lane":"A -> B","why":"one sentence with the numbers","metric":"$/mi","delta_pct":8.2}],'
        '"declining_lanes":[same shape],'
        '"market_shifts":[{"headline":"short statement","evidence":"the numbers that show it"}],'
        '"cost_levers":[{"lever":"short imperative","impact_hint":"expected effect, grounded in the numbers"}],'
        '"entity_insights":{"<key from the data>":["one specific, actionable suggestion for that state or lane"]}}. '
        f"Each of the four lists has 0-{MAX_ITEMS} items. Name concrete lanes and numbers; no generic advice and no "
        f"paragraphs. entity_insights keys MUST be copied from the `key` fields in the data; give at most "
        f"{MAX_PER_ENTITY} suggestions per key and skip keys you have nothing useful to say about. Say nothing "
        "that is not supported by the data.\n\n"
        + security_instruction("analyse")
        + "\n\n"
        + FENCE_OPEN
        + "\n"
        + json.dumps(agg, sort_keys=True, default=str)
        + "\n"
        + FENCE_CLOSE
    )


def _s(v: Any, n: int) -> str:
    return str(v or "").strip()[:n]


def _num(v: Any) -> float | None:
    try:
        return None if v is None or isinstance(v, bool) else round(float(v), 1)
    except (TypeError, ValueError):
        return None


def parse_output(text: str, allowed: set[str]) -> dict[str, Any] | None:
    """Strict-schema parse. ``None`` means unparseable (never invent)."""
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None
    lists = ("focus_lanes", "declining_lanes", "market_shifts", "cost_levers")
    if not all(isinstance(obj.get(k), list) for k in lists):
        return None

    def lane_items(key: str) -> list[LaneInsightItem]:
        out = []
        for it in obj[key]:
            if isinstance(it, dict) and _s(it.get("lane"), 1) and _s(it.get("why"), 1):
                ev = it.get("evidence") if isinstance(it.get("evidence"), dict) else {}
                out.append(LaneInsightItem(
                    lane=_s(it["lane"], 120), why=_s(it["why"], 300),
                    metric=_s(it.get("metric") or ev.get("metric"), 40),
                    delta_pct=_num(it.get("delta_pct", ev.get("delta_pct"))),
                ))
        return out[:MAX_ITEMS]

    shifts = [
        ShiftItem(headline=_s(i["headline"], 160), evidence=_s(i.get("evidence"), 300))
        for i in obj["market_shifts"] if isinstance(i, dict) and _s(i.get("headline"), 1)
    ][:MAX_ITEMS]
    levers = [
        LeverItem(lever=_s(i["lever"], 160), impact_hint=_s(i.get("impact_hint"), 300))
        for i in obj["cost_levers"] if isinstance(i, dict) and _s(i.get("lever"), 1)
    ][:MAX_ITEMS]
    ents: dict[str, list[str]] = {}
    raw_ents = obj.get("entity_insights")
    if isinstance(raw_ents, dict):
        for k, v in raw_ents.items():
            if k not in allowed:
                continue  # the model invented a key; drop it
            vals = [v] if isinstance(v, str) else v if isinstance(v, list) else []
            clean = [_s(x, 260) for x in vals if isinstance(x, str) and x.strip()][:MAX_PER_ENTITY]
            if clean:
                ents[k] = clean
    focus, decl = lane_items("focus_lanes"), lane_items("declining_lanes")
    if not (focus or decl or shifts or levers or ents):
        return None
    return {"focus_lanes": focus, "declining_lanes": decl, "market_shifts": shifts, "cost_levers": levers,
            "entity_insights": ents}


def _result_from_row(row: LaneInsightsCache, created: datetime) -> LaneAiInsights:
    return LaneAiInsights(
        status="ok", generated_at=created.isoformat(), cached=True,
        focus_lanes=[LaneInsightItem(**x) for x in row.focus_lanes or []],
        declining_lanes=[LaneInsightItem(**x) for x in row.declining_lanes or []],
        market_shifts=[ShiftItem(**x) for x in row.market_shifts or []],
        cost_levers=[LeverItem(**x) for x in row.cost_levers or []],
        entity_insights=row.entity_insights or {},
    )


async def get_cached_entity_insights(
    session: AsyncSession, scope: H.Scope, period: LanesPeriod
) -> dict[str, list[str]]:
    """Read-only helper: whatever suggestions are cached, no model call."""
    row = await repo.get_cache(session, scope.tenant_id, period_key(period, scope))
    return dict(row.entity_insights or {}) if row else {}


async def get_lane_insights(
    session: AsyncSession,
    scope: H.Scope,
    period: LanesPeriod,
    *,
    provider: Any | None,
    resolve_error: str | None = None,
    refresh: bool = False,
    now: datetime | None = None,
) -> LaneAiInsights:
    agg = await build_aggregates(session, scope, period, now)
    if agg["kpis"]["runs"] == 0:
        return LaneAiInsights(status="empty")
    input_hash = hashlib.sha256(json.dumps(agg, sort_keys=True, default=str).encode()).hexdigest()
    pkey = period_key(period, scope)
    row = await repo.get_cache(session, scope.tenant_id, pkey)
    stamp = datetime.now(UTC)
    if row is not None and not refresh and row.input_hash == input_hash:
        created = row.created_at if row.created_at.tzinfo else row.created_at.replace(tzinfo=UTC)
        if stamp - created < CACHE_TTL:
            return _result_from_row(row, created)

    def unavailable(err: str) -> LaneAiInsights:
        return LaneAiInsights(status="unavailable", ai_error=err)

    if resolve_error:
        return unavailable(resolve_error)
    if provider is None or getattr(provider, "kind", None) == "null":
        return unavailable("provider_null")
    try:
        call = await asyncio.wait_for(provider.generate_text(_build_prompt(agg)), timeout=LANES_AI_TIMEOUT_S)
    except TimeoutError:
        return unavailable(f"timeout:{LANES_AI_TIMEOUT_S:g}s")
    except Exception as exc:  # noqa: BLE001
        log.warning("lane insights provider error %s: %s", type(exc).__name__, exc)
        return unavailable(f"error:{type(exc).__name__}")
    text = (getattr(call, "text", None) or "").strip()
    if getattr(call, "status", None) != "ok" or not text:
        return unavailable(f"empty_response:{getattr(call, 'status', None)}")
    parsed = parse_output(text, allowed_entity_keys(agg))
    if parsed is None:
        return unavailable("unparseable_response")

    model = (getattr(call, "model", None) or getattr(provider, "model", "") or "")[:64]
    payload = {
        "focus_lanes": [x.model_dump() for x in parsed["focus_lanes"]],
        "declining_lanes": [x.model_dump() for x in parsed["declining_lanes"]],
        "market_shifts": [x.model_dump() for x in parsed["market_shifts"]],
        "cost_levers": [x.model_dump() for x in parsed["cost_levers"]],
        "entity_insights": parsed["entity_insights"],
    }
    if row is None:
        repo.add_cache(session, tenant_id=scope.tenant_id, period_key=pkey, input_hash=input_hash,
                       model=model, **payload)
    else:
        row.input_hash, row.model, row.created_at = input_hash, model, stamp
        for k, v in payload.items():
            setattr(row, k, v)
    await session.commit()
    return LaneAiInsights(
        status="ok", generated_at=stamp.isoformat(), cached=False,
        focus_lanes=parsed["focus_lanes"], declining_lanes=parsed["declining_lanes"],
        market_shifts=parsed["market_shifts"], cost_levers=parsed["cost_levers"],
        entity_insights=parsed["entity_insights"],
    )
