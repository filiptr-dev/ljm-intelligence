"""Seed ~2000 deterministic demo freight runs over the last 24 months.

Revision ID: 0036
Revises: 0035
Create Date: 2026-10-09

Idempotent: inserts only when the LJM tenant has no ``source='demo'`` run.
Every row carries ``source='demo'`` and ``raw.demo=True``; the downgrade
deletes demo rows only. Deterministic (``random.Random(20261009)``) relative
to the day the migration runs.

Signal baked in so the AI (and the eye) has something to find:

* five dominant lanes carry ~60% of volume;
* a market shift: months -8..0 ("post") Dallas->Memphis rate/mi -11% and
  volume ~-30%, Chicago->Atlanta rate/mi +8% and volume ~+25%;
* Laredo->Chicago (reefer) exists only in "post" and grows month over month;
* seasonality: reefers +25% May-Aug, flatbeds +15% Mar-Sep;
* fuel cost = miles x diesel $/gal (diesel_prices at the pickup week, else a
  bundled monthly fallback) / 6.5 mpg;
* city centroids and highway miles come from ``app.rates`` (``us_cities.json``).
"""

from __future__ import annotations

import bisect
import random
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

LJM_TENANT_ID = "01LJMORGLJM00000000000000A"
TOTAL_RUNS = 2000
HISTORY_DAYS = 730
POST_DAYS = 240  # "post" = the last ~8 months
MPG = 6.5
DRIVER_PAY = {"van": 0.55, "reefer": 0.60, "flatbed": 0.62, "stepdeck": 0.65}

# (origin city, st, dest city, st, equipment, weight, base $/mi, group)
_DOMINANT = [
    ("Chicago", "IL", "Atlanta", "GA", "van", 18.0, 2.30, "chi-atl"),
    ("Dallas", "TX", "Memphis", "TN", "reefer", 14.0, 2.55, "dal-mem"),
    ("Columbus", "OH", "Charlotte", "NC", "van", 11.0, 2.45, "cmh-clt"),
    ("Los Angeles", "CA", "Phoenix", "AZ", "reefer", 9.0, 2.60, "lax-phx"),
    ("Harrisburg", "PA", "Jacksonville", "FL", "flatbed", 8.0, 2.20, "hbg-jax"),
]
_LAREDO = ("Laredo", "TX", "Chicago", "IL", "reefer", 3.0, 2.20, "lrd-chi")
# Long tail: short ~11, medium ~14, long ~12 weight (matches ~45/40/15 bands).
_TAIL = [
    ("Indianapolis", "IN", "Detroit", "MI", "van", 1.6, 2.75),
    ("Houston", "TX", "Dallas", "TX", "van", 1.6, 2.80),
    ("Atlanta", "GA", "Charlotte", "NC", "van", 1.6, 2.75),
    ("Phoenix", "AZ", "Las Vegas", "NV", "reefer", 1.6, 2.85),
    ("Seattle", "WA", "Portland", "OR", "flatbed", 1.6, 2.90),
    ("Newark", "NJ", "Boston", "MA", "van", 1.5, 2.95),
    ("Birmingham", "AL", "Atlanta", "GA", "van", 1.5, 2.85),
    ("Atlanta", "GA", "Dallas", "TX", "van", 1.75, 2.30),
    ("Philadelphia", "PA", "Atlanta", "GA", "van", 1.75, 2.35),
    ("Houston", "TX", "Memphis", "TN", "reefer", 1.75, 2.45),
    ("St. Louis", "MO", "Dallas", "TX", "flatbed", 1.75, 2.35),
    ("Detroit", "MI", "Charlotte", "NC", "van", 1.75, 2.30),
    ("Denver", "CO", "Omaha", "NE", "flatbed", 1.75, 2.35),
    ("Birmingham", "AL", "Dallas", "TX", "stepdeck", 1.75, 2.40),
    ("Albuquerque", "NM", "Dallas", "TX", "van", 1.75, 2.35),
    ("Newark", "NJ", "Miami", "FL", "van", 1.7, 2.05),
    ("Los Angeles", "CA", "Dallas", "TX", "reefer", 1.7, 2.15),
    ("Seattle", "WA", "Denver", "CO", "flatbed", 1.7, 2.10),
    ("Chicago", "IL", "Dallas", "TX", "van", 1.7, 2.10),
    ("Atlanta", "GA", "Denver", "CO", "stepdeck", 1.7, 2.10),
    ("Phoenix", "AZ", "Houston", "TX", "reefer", 1.7, 2.15),
    ("Miami", "FL", "Chicago", "IL", "reefer", 1.7, 2.10),
]
_BROKERS = [
    "Blue Ridge Logistics", "Keystone Freight Group", "Peach State Haulers", "Great Lakes Express",
    "Carolina Dispatch Co", "Hudson Valley Reefer", "Delta Flatbed Partners", "Appalachian Logistics",
    "Lone Star Freight", "Pacific Crest Brokerage",
]

# Fallback national diesel $/gal by month (index 0 = January), 2024..2026.
_DIESEL_FALLBACK = {
    2024: [3.95, 4.05, 4.00, 4.10, 4.05, 3.95, 3.85, 3.80, 3.70, 3.65, 3.60, 3.55],
    2025: [3.60, 3.65, 3.55, 3.50, 3.45, 3.50, 3.55, 3.60, 3.55, 3.50, 3.45, 3.50],
    2026: [3.55, 3.60, 3.65, 3.70, 3.75, 3.80, 3.75, 3.70, 3.70, 3.65, 3.60, 3.60],
}


def _fallback_diesel(d: date) -> float:
    table = _DIESEL_FALLBACK.get(d.year) or _DIESEL_FALLBACK[max(_DIESEL_FALLBACK)]
    return table[d.month - 1]


def _season(equipment: str, month: int) -> float:
    if equipment == "reefer" and 5 <= month <= 8:
        return 1.25
    if equipment == "flatbed" and 3 <= month <= 9:
        return 1.15
    return 1.0


def generate_runs(
    now: datetime,
    diesel_for: Callable[[str, date], float | None],
    resolve: Callable[[str, str], tuple[float, float]],
    highway_miles: Callable[[float, float, float, float], int],
) -> list[dict[str, Any]]:
    """Pure + deterministic: same ``now`` and lookups -> same rows."""
    rng = random.Random(20261009)
    lanes: list[dict[str, Any]] = []
    for oc, os_, dc, ds, eq, w, rpm, grp in [*_DOMINANT, _LAREDO]:
        lanes.append({"oc": oc, "os": os_, "dc": dc, "ds": ds, "eq": eq, "w": w, "rpm": rpm, "grp": grp})
    for oc, os_, dc, ds, eq, w, rpm in _TAIL:
        lanes.append({"oc": oc, "os": os_, "dc": dc, "ds": ds, "eq": eq, "w": w, "rpm": rpm,
                      "grp": f"tail-{oc.lower().replace(' ', '')}-{dc.lower().replace(' ', '')}"})
    for ln in lanes:
        o_lat, o_lng = resolve(ln["oc"], ln["os"])
        d_lat, d_lng = resolve(ln["dc"], ln["ds"])
        ln.update(o_lat=o_lat, o_lng=o_lng, d_lat=d_lat, d_lng=d_lng,
                  base_miles=highway_miles(o_lat, o_lng, d_lat, d_lng))

    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    rows: list[dict[str, Any]] = []
    for _ in range(TOTAL_RUNS):
        days_ago = rng.randrange(0, HISTORY_DAYS)
        day = today - timedelta(days=days_ago)
        post = days_ago < POST_DAYS
        weights = []
        for ln in lanes:
            w = ln["w"] * _season(ln["eq"], day.month)
            g = ln["grp"]
            if g == "dal-mem" and post:
                w *= 0.70
            elif g == "chi-atl" and post:
                w *= 1.25
            elif g == "lrd-chi":
                # only in "post", ramping up month over month toward today
                w = w * (1.0 + (POST_DAYS - days_ago) / 40.0) if post else 0.0
            weights.append(w)
        ln = rng.choices(lanes, weights=weights, k=1)[0]

        miles = max(40, round(ln["base_miles"] * rng.uniform(0.97, 1.03)))
        rpm = ln["rpm"] * 0.88 * rng.uniform(0.94, 1.06)  # 0.88: keeps gross margin in a believable band
        if ln["grp"] == "dal-mem" and post:
            rpm *= 0.89
        elif ln["grp"] == "chi-atl" and post:
            rpm *= 1.08
        revenue = round(miles * rpm, 2)

        diesel = diesel_for(ln["os"], day.date())
        if diesel is None:
            diesel = _fallback_diesel(day.date())
        fuel = round(miles * diesel / MPG, 2)
        driver = round(miles * DRIVER_PAY[ln["eq"]] * rng.uniform(0.95, 1.05), 2)
        detention = rng.uniform(40, 160) if rng.random() < 0.3 else 0.0
        load = round(35 + miles * 0.035 + detention + rng.uniform(0, 25), 2)
        dispatch = round(max(75.0, revenue * 0.05), 2)

        deadhead = rng.randint(0, min(120, 30 + miles // 12))
        pickup = day + timedelta(hours=rng.randint(6, 18), minutes=rng.choice((0, 15, 30, 45)))
        delivery = pickup + timedelta(hours=max(6, round(miles / 50.0)) + rng.randint(0, 6))
        rows.append({
            "tenant_id": LJM_TENANT_ID,
            "source": "demo",
            "broker_name": rng.choice(_BROKERS),
            "broker_lead_id": None,
            "origin_city": ln["oc"], "origin_state": ln["os"],
            "dest_city": ln["dc"], "dest_state": ln["ds"],
            "origin_lat": ln["o_lat"], "origin_lng": ln["o_lng"],
            "dest_lat": ln["d_lat"], "dest_lng": ln["d_lng"],
            "miles": miles, "deadhead_miles": deadhead, "equipment": ln["eq"],
            "pickup_at": pickup, "delivery_at": delivery,
            "revenue_usd": revenue, "cost_fuel_usd": fuel, "cost_driver_usd": driver,
            "cost_load_usd": load, "cost_dispatch_usd": dispatch,
            "raw": {"demo": True, "lane_group": ln["grp"], "shift_arc": "post" if post else "pre"},
        })
    rows.sort(key=lambda r: r["pickup_at"])
    return rows


_RUNS = sa.table(
    "freight_runs",
    *[sa.column(c) for c in (
        "tenant_id", "source", "broker_name", "broker_lead_id", "origin_city", "origin_state",
        "dest_city", "dest_state", "origin_lat", "origin_lng", "dest_lat", "dest_lng", "miles",
        "deadhead_miles", "equipment", "pickup_at", "delivery_at", "revenue_usd", "cost_fuel_usd",
        "cost_driver_usd", "cost_load_usd", "cost_dispatch_usd",
    )],
    sa.column("raw", sa.JSON()),
)


def upgrade() -> None:
    from app.rates.domain import CityState, haversine_miles, highway_miles as _hw, padd_for, resolve_city

    bind = op.get_bind()
    existing = bind.execute(
        sa.text("SELECT COUNT(*) FROM freight_runs WHERE tenant_id = :t AND source = 'demo'"),
        {"t": LJM_TENANT_ID},
    ).scalar() or 0
    if existing:
        return

    # diesel_prices: (padd -> sorted week list, prices); empty -> bundled fallback.
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
            return None  # stale/absent for that week -> deterministic fallback
        return prices[i]

    def resolve(city: str, state: str) -> tuple[float, float]:
        p = resolve_city(CityState(city, state))
        return p.lat, p.lon

    def hw(a_lat: float, a_lng: float, b_lat: float, b_lng: float) -> int:
        return _hw(haversine_miles(a_lat, a_lng, b_lat, b_lng))

    rows = generate_runs(datetime.now(UTC), diesel_for, resolve, hw)
    for i in range(0, len(rows), 250):
        op.bulk_insert(_RUNS, rows[i : i + 250])


def downgrade() -> None:
    op.get_bind().execute(
        sa.text("DELETE FROM freight_runs WHERE tenant_id = :t AND source = 'demo'"),
        {"t": LJM_TENANT_ID},
    )
