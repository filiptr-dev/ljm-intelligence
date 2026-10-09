"""Rates — HTTP DTOs. Thin pydantic wrappers over the domain VOs. Every
response carries `evidence: list[str]` so the UI shows *why* (teaches the
user the formula)."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

Equipment = Literal["V", "R", "F", "ANY"]


class CityStateIn(BaseModel):
    city: str = Field(min_length=1, max_length=128)
    state: str = Field(min_length=2, max_length=2)


class RateQuoteIn(BaseModel):
    origin: CityStateIn
    dest: CityStateIn
    equipment: Equipment = "V"


class DieselRowOut(BaseModel):
    padd: str
    week_of: str
    price_usd: float
    stale: bool
    fallback_padd: bool


class CompRowOut(BaseModel):
    rate_usd: float
    miles: int
    rate_per_mile: float
    origin: str
    dest: str
    equipment: str | None
    pickup_date: str | None


class RateQuoteOut(BaseModel):
    origin: CityStateIn
    dest: CityStateIn
    equipment: Equipment
    great_circle_miles: int
    highway_miles: int
    highway_factor: float
    diesel: DieselRowOut | None
    rate_band: dict  # {low, mid, high} $/mi
    rate_band_dollars: dict  # {low, mid, high} total $
    comps: list[CompRowOut]
    comps_count: int
    evidence: list[str]


class ProfitIn(BaseModel):
    rate_usd: float = Field(gt=0)
    loaded_miles: int = Field(gt=0)
    deadhead_miles: int = Field(ge=0, default=0)
    mpg: float = Field(gt=0, default=6.5)
    equipment: Equipment = "V"
    origin_state: str = Field(min_length=2, max_length=2)
    dest_state: str = Field(min_length=2, max_length=2)


class ProfitOut(BaseModel):
    rate_usd: float
    loaded_miles: int
    deadhead_miles: int
    mpg: float
    diesel_per_gal: float
    diesel_stale: bool
    fuel_cost: float
    driver_cost: float
    toll_cost: float
    toll_corridor: str | None
    deadhead_cost: float
    total_cost: float
    net: float
    margin_pct: float
    verdict: Literal["green", "tight", "red"]
    verdict_thresholds: dict
    evidence: list[str]


class BackhaulIn(BaseModel):
    drop: CityStateIn
    home_state: str = Field(min_length=2, max_length=2)
    equipment: Equipment = "ANY"
    radius_miles: int = Field(default=150, ge=25, le=500)


class BackhaulCandidateOut(BaseModel):
    lead_id: str
    name: str
    kind: str
    city: str
    state: str
    distance_miles: int
    toward_home: bool
    headed_home_score: int
    expected_rate_per_mile: float | None
    has_phone: bool
    has_email: bool
    last_contacted_at: str | None
    phone: str | None
    email: str | None


class BackhaulOut(BaseModel):
    drop: CityStateIn
    home_state: str
    equipment: Equipment
    radius_miles: int
    candidates: list[BackhaulCandidateOut]
    evidence: list[str]
