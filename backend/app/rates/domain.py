"""Rates domain — pure value objects. Zero I/O.

All three rate tools (lane quote / load profit / backhaul) ultimately come
down to a formula over distance, diesel, and a handful of constants. One
unit-testable file keeps them from drifting apart.

Constants are named in one place so a bump to driver pay or MPG lands in a
single commit, and so the UI can quote the number back to the user (teaches
them *why* the margin is what it is).

City centroids in ``data/us_cities.json`` are derived from the US Census
2023 Gazetteer PLACES file
(https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2023_Gazetteer/2023_Gaz_place_national.zip);
Census legal suffixes (" city", " town", " village", " CDP", etc.) are
stripped so names match the way dispatchers type them.
"""
from __future__ import annotations

import json
import math
import pathlib
from dataclasses import dataclass
from typing import Final, Literal

# ---- constants -----------------------------------------------------------
# Highway/circuity factor — great-circle miles → real highway miles. Industry
# rule-of-thumb; references put it ~1.15–1.20. One constant, one knob.
HIGHWAY_FACTOR: Final[float] = 1.17

# Default truck fuel economy — override on the Load Profit form.
DEFAULT_MPG: Final[float] = 6.5

# Driver pay — US owner-op / company-driver range. $0.55 is a reasonable
# all-in mid-point for long-haul van/reefer in 2026.
DRIVER_PAY_PER_LOADED_MILE: Final[float] = 0.55
DRIVER_PAY_PER_DEADHEAD_MILE: Final[float] = 0.35

# Toll corridor table — v1 is a tiny constant lookup, keyed by (origin_state,
# dest_state) proxying "likely corridor". Zero when the pair isn't in a
# known corridor. The UI shows the row so the user sees it.
TOLL_CORRIDORS: Final[dict[tuple[str, str], tuple[str, float]]] = {
    ("NY", "FL"): ("I-95", 65.0),
    ("FL", "NY"): ("I-95", 65.0),
    ("NY", "GA"): ("I-95", 55.0),
    ("GA", "NY"): ("I-95", 55.0),
    ("NJ", "FL"): ("I-95", 60.0),
    ("FL", "NJ"): ("I-95", 60.0),
    ("CA", "NY"): ("I-80", 45.0),
    ("NY", "CA"): ("I-80", 45.0),
    ("NY", "OH"): ("I-80 / PA Turnpike", 55.0),
    ("OH", "NY"): ("I-80 / PA Turnpike", 55.0),
    ("PA", "OH"): ("PA Turnpike", 30.0),
    ("OH", "PA"): ("PA Turnpike", 30.0),
    ("NJ", "IL"): ("PA Turnpike / I-80", 50.0),
    ("IL", "NJ"): ("PA Turnpike / I-80", 50.0),
}

# Verdict thresholds for Load Profit. Fixed here so the UI can echo them.
VERDICT_GREEN_PCT: Final[float] = 15.0
VERDICT_TIGHT_PCT: Final[float] = 5.0

Verdict = Literal["green", "tight", "red"]
Equipment = Literal["V", "R", "F"]  # Van / Reefer / Flatbed

# PADD assignment per US state. EIA publishes diesel by PADD district; this
# maps a destination state to the district whose price should feed the lane.
# Source: EIA PADD definitions.
STATE_TO_PADD: Final[dict[str, str]] = {
    # PADD 1A — New England
    "CT": "1A", "ME": "1A", "MA": "1A", "NH": "1A", "RI": "1A", "VT": "1A",
    # PADD 1B — Central Atlantic
    "DE": "1B", "DC": "1B", "MD": "1B", "NJ": "1B", "NY": "1B", "PA": "1B",
    # PADD 1C — Lower Atlantic
    "FL": "1C", "GA": "1C", "NC": "1C", "SC": "1C", "VA": "1C", "WV": "1C",
    # PADD 2 — Midwest
    "IL": "2", "IN": "2", "IA": "2", "KS": "2", "KY": "2", "MI": "2",
    "MN": "2", "MO": "2", "NE": "2", "ND": "2", "OH": "2", "OK": "2",
    "SD": "2", "TN": "2", "WI": "2",
    # PADD 3 — Gulf Coast
    "AL": "3", "AR": "3", "LA": "3", "MS": "3", "NM": "3", "TX": "3",
    # PADD 4 — Rocky Mountain
    "CO": "4", "ID": "4", "MT": "4", "UT": "4", "WY": "4",
    # PADD 5 — West Coast
    "AK": "5", "AZ": "5", "CA": "5", "HI": "5", "NV": "5", "OR": "5", "WA": "5",
}


def padd_for(state: str) -> str:
    """Return the PADD district for a US state abbr. Defaults to '3' (Gulf
    Coast — the mid-national price) when a state is unknown, so a diesel row
    is always returnable rather than 500ing on a typo. The service badges
    the fallback so the UI shows what happened."""
    return STATE_TO_PADD.get(state.upper(), "3")


# ---- lane / distance -----------------------------------------------------
_CITIES_CACHE: list[dict] | None = None


def _cities() -> list[dict]:
    """Lazy-loaded, module-level city centroid table (public domain subset).
    Reading on first call keeps import time flat; subsequent calls are O(1).
    """
    global _CITIES_CACHE
    if _CITIES_CACHE is None:
        path = pathlib.Path(__file__).parent / "data" / "us_cities.json"
        with path.open() as f:
            _CITIES_CACHE = json.load(f)
    return _CITIES_CACHE


@dataclass(frozen=True)
class CityState:
    city: str
    state: str


@dataclass(frozen=True)
class CityPoint:
    city: str
    state: str
    lat: float
    lon: float


class LaneResolveError(ValueError):
    pass


def resolve_city(cs: CityState) -> CityPoint:
    """City + state → CityPoint. Case-insensitive. Raises `LaneResolveError`
    with a human message when the centroid list doesn't have the city — the
    router turns that into a 422 so the UI can show "city not in index"."""
    want_city = (cs.city or "").strip().lower()
    want_state = (cs.state or "").strip().upper()
    for row in _cities():
        if row["city"].lower() == want_city and row["state"].upper() == want_state:
            return CityPoint(
                city=row["city"], state=row["state"], lat=row["lat"], lon=row["lon"]
            )
    raise LaneResolveError(f"{cs.city}, {cs.state} not in city centroid index")


def haversine_miles(a_lat: float, a_lon: float, b_lat: float, b_lon: float) -> float:
    """Great-circle miles between two WGS84 coordinates. Earth radius =
    3958.8 mi (mean)."""
    r = 3958.8
    phi1, phi2 = math.radians(a_lat), math.radians(b_lat)
    dphi = math.radians(b_lat - a_lat)
    dlam = math.radians(b_lon - a_lon)
    h = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def highway_miles(gc_miles: float) -> int:
    """Great-circle → highway miles via `HIGHWAY_FACTOR`, rounded to int."""
    return round(gc_miles * HIGHWAY_FACTOR)


@dataclass(frozen=True)
class Lane:
    origin: CityPoint
    dest: CityPoint

    @property
    def great_circle_miles(self) -> float:
        return haversine_miles(self.origin.lat, self.origin.lon, self.dest.lat, self.dest.lon)

    @property
    def highway_miles(self) -> int:
        return highway_miles(self.great_circle_miles)


# ---- rate band -----------------------------------------------------------
@dataclass(frozen=True)
class RateBand:
    low: float
    mid: float
    high: float

    def __post_init__(self) -> None:  # pragma: no cover - trivial
        if not (self.low <= self.mid <= self.high):
            raise ValueError("RateBand invariant: low <= mid <= high")


def band_from_comps(comps: list[float], fallback_mid: float) -> RateBand:
    """Band from historical comps if we have any, else a widened fallback
    around `fallback_mid` ($/mi) so the UI still shows a plausible range
    with a "diesel + highway only" badge."""
    if comps:
        xs = sorted(comps)
        n = len(xs)
        def pct(p: float) -> float:
            idx = min(n - 1, max(0, round(p * (n - 1))))
            return xs[idx]
        return RateBand(low=pct(0.25), mid=pct(0.5), high=pct(0.75))
    # No comps — fuel-only fallback. ±20% around the fuel-break-even midpoint
    # is deliberately wide; the UI shows the badge so the user treats it as a
    # floor, not a quote.
    return RateBand(low=round(fallback_mid * 0.90, 2),
                    mid=round(fallback_mid, 2),
                    high=round(fallback_mid * 1.25, 2))


# ---- profit --------------------------------------------------------------
@dataclass(frozen=True)
class ProfitBreakdown:
    rate: float
    loaded_miles: int
    deadhead_miles: int
    diesel_per_gal: float
    mpg: float
    fuel_cost: float
    driver_cost: float
    toll_cost: float
    deadhead_cost: float
    total_cost: float
    net: float
    margin_pct: float
    verdict: Verdict
    toll_corridor: str | None


def score_profit(
    *,
    rate: float,
    loaded_miles: int,
    deadhead_miles: int,
    diesel_per_gal: float,
    mpg: float,
    origin_state: str,
    dest_state: str,
) -> ProfitBreakdown:
    """Pure margin math. `diesel_per_gal` may be stale — the service tags
    staleness; the formula doesn't care."""
    if mpg <= 0:
        raise ValueError("mpg must be > 0")
    total_miles = max(0, loaded_miles) + max(0, deadhead_miles)
    fuel_cost = round((total_miles / mpg) * diesel_per_gal, 2)
    driver_cost = round(
        loaded_miles * DRIVER_PAY_PER_LOADED_MILE
        + deadhead_miles * DRIVER_PAY_PER_DEADHEAD_MILE, 2
    )
    toll_corridor, toll_cost = TOLL_CORRIDORS.get(
        (origin_state.upper(), dest_state.upper()), (None, 0.0)
    )
    deadhead_cost = round(
        deadhead_miles * DRIVER_PAY_PER_DEADHEAD_MILE
        + (deadhead_miles / mpg) * diesel_per_gal, 2
    )
    total_cost = round(fuel_cost + driver_cost + toll_cost, 2)
    net = round(rate - total_cost, 2)
    margin_pct = round((net / rate) * 100, 1) if rate > 0 else 0.0
    if margin_pct >= VERDICT_GREEN_PCT:
        verdict: Verdict = "green"
    elif margin_pct >= VERDICT_TIGHT_PCT:
        verdict = "tight"
    else:
        verdict = "red"
    return ProfitBreakdown(
        rate=rate,
        loaded_miles=loaded_miles,
        deadhead_miles=deadhead_miles,
        diesel_per_gal=diesel_per_gal,
        mpg=mpg,
        fuel_cost=fuel_cost,
        driver_cost=driver_cost,
        toll_cost=toll_cost,
        deadhead_cost=deadhead_cost,
        total_cost=total_cost,
        net=net,
        margin_pct=margin_pct,
        verdict=verdict,
        toll_corridor=toll_corridor,
    )
