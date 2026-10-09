"""Reseed the LJM demo runs with a market shift that shows in the month AND year views.

Revision ID: 0037
Revises: 0036
Create Date: 2026-10-09

0036 is already pushed (and may have run on prod), so it is left untouched. The
page compares the *recent half* with the half before it: week = 6.5 vs 6.5
weeks, month = 6 vs 6 months, year = 12 vs 12 months. 0036 put its shift at
"the last 240 days", which diluted both views (Dallas->Memphis -6% / -9.5%) and
gave Laredo->Chicago no year baseline.

Here every signal is a three-step ladder on the run's age so that BOTH
comparisons land on the same percentage (A = newest 6 months, B = months 6-12,
C = older than 12 months; month view = A vs B, year view = (A+B)/2 vs C):

* Dallas->Memphis   rate/mi  A .838  B .942  C 1.0   -> about -11% in both views
* Chicago->Atlanta  rate/mi  A 1.122 B 1.039 C 1.0   -> about +8% in both views
* Laredo->Chicago   rate/mi  A 1.152 B 1.048 C 1.0   -> about +10% in both views,
  and it now has runs in every third (a year baseline) with volume rising.

Everything else (lane list, seasonality, cost model, banding) is the 0036
generator's, loaded from that file so it is not copied. Upgrade deletes and
reseeds ``source='demo'`` rows of the LJM tenant only (real rows are never
touched) and clears the LJM ``lane_insights_cache`` so the AI regenerates.
Downgrade restores the 0036 data shape the same way.
"""

from __future__ import annotations

import importlib.util
import random
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PREV = Path(__file__).with_name("0036_demo_freight_runs.py")


def _load_0036() -> Any:
    spec = importlib.util.spec_from_file_location("mig0036_shared", _PREV)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_M = _load_0036()
LJM_TENANT_ID = _M.LJM_TENANT_ID

# age bucket -> (rate multiplier, volume multiplier); bucket by days since the run
_RATE = {
    "dal-mem": (0.838, 0.942, 1.0),
    "chi-atl": (1.122, 1.039, 1.0),
    "lrd-chi": (1.152, 1.048, 1.0),
}
_VOLUME = {
    "dal-mem": (0.65, 0.80, 1.0),
    "chi-atl": (1.35, 1.15, 1.0),
    "lrd-chi": (2.2, 1.3, 0.6),
}
_A_DAYS, _B_DAYS = 182, 365
_LAREDO_WEIGHT = 4.0


def _bucket(days_ago: int) -> int:
    return 0 if days_ago < _A_DAYS else 1 if days_ago < _B_DAYS else 2


def generate_runs(
    now: datetime,
    diesel_for: Callable[[str, date], float | None],
    resolve: Callable[[str, str], tuple[float, float]],
    highway_miles: Callable[[float, float, float, float], int],
) -> list[dict[str, Any]]:
    """Pure + deterministic: same ``now`` and lookups -> same rows."""
    m = _M
    rng = random.Random(20261009)
    lanes: list[dict[str, Any]] = []
    for oc, os_, dc, ds, eq, w, rpm, grp in [*m._DOMINANT, m._LAREDO]:
        w = _LAREDO_WEIGHT if grp == "lrd-chi" else w
        lanes.append({"oc": oc, "os": os_, "dc": dc, "ds": ds, "eq": eq, "w": w, "rpm": rpm, "grp": grp})
    for oc, os_, dc, ds, eq, w, rpm in m._TAIL:
        lanes.append({"oc": oc, "os": os_, "dc": dc, "ds": ds, "eq": eq, "w": w, "rpm": rpm,
                      "grp": f"tail-{oc.lower().replace(' ', '')}-{dc.lower().replace(' ', '')}"})
    for ln in lanes:
        o_lat, o_lng = resolve(ln["oc"], ln["os"])
        d_lat, d_lng = resolve(ln["dc"], ln["ds"])
        ln.update(o_lat=o_lat, o_lng=o_lng, d_lat=d_lat, d_lng=d_lng,
                  base_miles=highway_miles(o_lat, o_lng, d_lat, d_lng))

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    for _ in range(m.TOTAL_RUNS):
        days_ago = rng.randrange(0, m.HISTORY_DAYS)
        day = today - timedelta(days=days_ago)
        b = _bucket(days_ago)
        weights = []
        for ln in lanes:
            w = ln["w"] * m._season(ln["eq"], day.month)
            if ln["grp"] in _VOLUME:
                w *= _VOLUME[ln["grp"]][b]
            weights.append(w)
        ln = rng.choices(lanes, weights=weights, k=1)[0]

        miles = max(40, round(ln["base_miles"] * rng.uniform(0.97, 1.03)))
        rpm = ln["rpm"] * 0.88 * rng.uniform(0.94, 1.06)  # 0.88: keeps gross margin in a believable band
        if ln["grp"] in _RATE:
            rpm *= _RATE[ln["grp"]][b]
        revenue = round(miles * rpm, 2)

        diesel = diesel_for(ln["os"], day.date())
        if diesel is None:
            diesel = m._fallback_diesel(day.date())
        fuel = round(miles * diesel / m.MPG, 2)
        driver = round(miles * m.DRIVER_PAY[ln["eq"]] * rng.uniform(0.95, 1.05), 2)
        detention = rng.uniform(40, 160) if rng.random() < 0.3 else 0.0
        load = round(35 + miles * 0.035 + detention + rng.uniform(0, 25), 2)
        dispatch = round(max(75.0, revenue * 0.05), 2)

        deadhead = rng.randint(0, min(120, 30 + miles // 12))
        pickup = day + timedelta(hours=rng.randint(6, 18), minutes=rng.choice((0, 15, 30, 45)))
        delivery = pickup + timedelta(hours=max(6, round(miles / 50.0)) + rng.randint(0, 6))
        rows.append({
            "tenant_id": LJM_TENANT_ID,
            "source": "demo",
            "broker_name": rng.choice(m._BROKERS),
            "broker_lead_id": None,
            "origin_city": ln["oc"], "origin_state": ln["os"],
            "dest_city": ln["dc"], "dest_state": ln["ds"],
            "origin_lat": ln["o_lat"], "origin_lng": ln["o_lng"],
            "dest_lat": ln["d_lat"], "dest_lng": ln["d_lng"],
            "miles": miles, "deadhead_miles": deadhead, "equipment": ln["eq"],
            "pickup_at": pickup, "delivery_at": delivery,
            "revenue_usd": revenue, "cost_fuel_usd": fuel, "cost_driver_usd": driver,
            "cost_load_usd": load, "cost_dispatch_usd": dispatch,
            "raw": {"demo": True, "lane_group": ln["grp"], "shift_arc": ("post", "post", "pre")[b], "age_bucket": "ABC"[b]},
        })
    rows.sort(key=lambda r: r["pickup_at"])
    return rows


def _reseed(generate: Callable[..., list[dict[str, Any]]]) -> None:
    import bisect

    from app.rates.domain import CityState, haversine_miles, highway_miles as _hw, padd_for, resolve_city

    bind = op.get_bind()
    t = {"t": LJM_TENANT_ID}
    bind.execute(sa.text("DELETE FROM freight_runs WHERE tenant_id = :t AND source = 'demo'"), t)
    bind.execute(sa.text("DELETE FROM lane_insights_cache WHERE tenant_id = :t"), t)

    series: dict[str, tuple[list[date], list[float]]] = {}
    for padd, week_of, price in bind.execute(
        sa.text("SELECT padd, week_of, price_usd FROM diesel_prices ORDER BY padd, week_of")
    ).fetchall():
        wk = week_of if isinstance(week_of, date) and not isinstance(week_of, datetime) else date.fromisoformat(str(week_of)[:10])
        weeks, prices = series.setdefault(padd, ([], []))
        weeks.append(wk)
        prices.append(float(price))

    def diesel_for(state: str, day: date) -> float | None:
        weeks_prices = series.get(padd_for(state))
        if not weeks_prices:
            return None
        weeks, prices = weeks_prices
        i = bisect.bisect_right(weeks, day) - 1
        if i < 0 or (day - weeks[i]).days > 45:
            return None
        return prices[i]

    def resolve(city: str, state: str) -> tuple[float, float]:
        p = resolve_city(CityState(city, state))
        return p.lat, p.lon

    def hw(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> int:
        return _hw(haversine_miles(a_lat, a_lng, b_lat, b_lng))

    rows = generate(datetime.now(UTC), diesel_for, resolve, hw)
    for i in range(0, len(rows), 250):
        op.bulk_insert(_M._RUNS, rows[i : i + 250])


def upgrade() -> None:
    _reseed(generate_runs)


def downgrade() -> None:
    _reseed(_M.generate_runs)  # back to the 0036 data shape
