"""analysis — DTOs (pydantic request/response models).

Added by the 2026-10-08 onion/SOLID refactor. New router↔service shapes
live here instead of being hand-rolled per endpoint. Existing routers
keep their inline pydantic models for now; this file is the home for
new shared DTOs. See the plan: projects/ljm-intelligence/plan/2026-10-08-architecture-onion-solid.md.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

# ---- Lanes history (plan 2026-10-09-lanes-history-analysis) ----------------
LanesPeriod = Literal["week", "month", "year"]


class LaneMetrics(BaseModel):
    """Every number the page shows for one slice of runs. Derived in SQL sums."""

    runs: int = 0
    miles: int = 0
    revenue: float = 0.0
    cost_fuel: float = 0.0
    cost_driver: float = 0.0
    cost_load: float = 0.0
    cost_dispatch: float = 0.0
    cost_total: float = 0.0
    margin: float = 0.0
    margin_pct: float | None = None
    rate_per_mile: float | None = None
    cost_per_mile: float | None = None


class LaneBucket(BaseModel):
    key: str  # ISO date of the bucket start
    label: str
    metrics: LaneMetrics


class LengthBand(BaseModel):
    band: str
    runs: int
    pct: float
    revenue: float


class CostShare(BaseModel):
    key: Literal["fuel", "driver", "load", "dispatch"]
    label: str
    amount: float
    pct: float


class LaneTrend(BaseModel):
    """Recent half of the window vs the half before it."""

    label: str
    runs_pct: float | None = None
    revenue_pct: float | None = None
    rate_pct: float | None = None
    margin_pp: float | None = None


class LaneStatement(BaseModel):
    """One plain-language sentence next to a chart (amendment A1)."""

    key: str
    title: str
    text: str


class LaneRef(BaseModel):
    key: str  # filter key: "Chicago,IL>Atlanta,GA"
    label: str
    runs: int
    miles: int
    revenue: float
    rate_per_mile: float | None = None


class LanesSummary(BaseModel):
    period: LanesPeriod
    window_label: str
    window_start: str
    window_end: str
    history_runs: int
    history_months: int
    buckets: list[LaneBucket]
    kpis: LaneMetrics
    trend: LaneTrend
    cost_split: list[CostShare]
    length_bands: list[LengthBand]
    most_frequent_lane: LaneRef | None = None
    most_miles_lane: LaneRef | None = None
    most_revenue_lane: LaneRef | None = None
    shifting: bool = False
    statements: list[LaneStatement]


class TopLane(BaseModel):
    key: str
    origin: str
    dest: str
    runs: int
    miles: int
    revenue: float
    avg_rate_per_mi: float | None = None
    margin_pct: float | None = None
    trend_pct: float | None = None  # $/mi, recent half vs half before
    runs_trend_pct: float | None = None


class TopLanes(BaseModel):
    level: Literal["state", "city"]
    window_label: str
    lanes: list[TopLane]


class LaneEntity(BaseModel):
    """Everything the map popover shows for one state, city or lane arc."""

    key: str  # state:TX | city:Dallas,TX | lane:Dallas,TX>Memphis,TN
    kind: Literal["state", "city", "lane"]
    label: str
    metrics: LaneMetrics
    dominant_band: str | None = None
    trend: LaneTrend
    runs_as_origin: int | None = None
    runs_as_dest: int | None = None
    text: str


class HeatPoint(BaseModel):
    lat: float
    lng: float
    weight: float
    runs: int
    revenue: float
    key: str  # city entity key


class CityEntity(LaneEntity):
    lat: float
    lng: float


class HeatArc(BaseModel):
    key: str
    o_lat: float
    o_lng: float
    d_lat: float
    d_lng: float
    o_label: str
    d_label: str
    runs: int
    trend_pct: float | None = None
    entity: LaneEntity


class HeatmapData(BaseModel):
    period: LanesPeriod
    window_label: str
    origins: list[HeatPoint]
    dests: list[HeatPoint]
    cities: list[CityEntity]
    states: list[LaneEntity]
    arcs: list[HeatArc]


class RunRow(BaseModel):
    id: int
    pickup_at: str
    origin: str
    dest: str
    equipment: str | None = None
    miles: int
    revenue: float
    cost_fuel: float
    cost_driver: float
    cost_load: float
    cost_dispatch: float
    margin: float
    margin_pct: float | None = None


class RunsPage(BaseModel):
    items: list[RunRow]
    next_cursor: str | None = None
    total: int


class LaneInsightItem(BaseModel):
    lane: str
    why: str
    metric: str = ""
    delta_pct: float | None = None


class ShiftItem(BaseModel):
    headline: str
    evidence: str = ""


class LeverItem(BaseModel):
    lever: str
    impact_hint: str = ""


class LaneAiInsights(BaseModel):
    status: Literal["ok", "unavailable", "empty"]
    focus_lanes: list[LaneInsightItem] = Field(default_factory=list)
    declining_lanes: list[LaneInsightItem] = Field(default_factory=list)
    market_shifts: list[ShiftItem] = Field(default_factory=list)
    cost_levers: list[LeverItem] = Field(default_factory=list)
    entity_insights: dict[str, list[str]] = Field(default_factory=dict)
    generated_at: str | None = None
    cached: bool = False
    ai_error: str | None = None
