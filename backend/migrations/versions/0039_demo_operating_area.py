"""Keep the LJM demo runs, and the demo fleet, inside the carrier's operating area.

Revision ID: 0039
Revises: 0038
Create Date: 2026-10-09

The client drives the states east of Texas's eastern border (see
``app.analysis.operating_area``). 0036/0037 seeded Texas, Arizona, Colorado,
Washington... lanes, and 0038 parked two trucks' home bases in Dallas and
Phoenix. Those migrations may already have run, so they are left untouched.

Upgrade, for the LJM tenant only (real rows are never touched):

* deletes and reseeds ``source='demo'`` runs with the generator below;
* re-links ``truck_id`` the way 0038 does (same assignment function);
* moves demo trucks whose home base is outside the area into it and re-parks every
  demo truck at its latest run's origin (home base when it never ran);
* clears ``lane_insights_cache`` so the AI regenerates against the new data.

The market-shift story is unchanged in shape (three-step ladder on a run's age, so
the month AND year views agree):

* New Orleans->Memphis  (was Dallas->Memphis)    rate/mi  about -11%
* Chicago->Atlanta                               rate/mi  about +8%
* Jacksonville->Chicago (was Laredo->Chicago)    rate/mi  about +10%, volume rising

Lanes replaced vs 0036/0037 are listed in ``_REPLACED``. Downgrade reseeds with
the 0037 generator and re-links, so the data is valid for 0038 either way (home
bases stay where this migration put them: a home base is not history).
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

revision: str = "0039"
down_revision: str | None = "0038"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_DIR = Path(__file__).parent


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, _DIR / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


_M36 = _load("mig0036_shared", "0036_demo_freight_runs.py")
_M37 = _load("mig0037_shared", "0037_reseed_demo_freight_runs.py")
_M38 = _load("mig0038_shared", "0038_demo_fleet.py")
LJM_TENANT_ID = _M36.LJM_TENANT_ID

# (origin city, st, dest city, st, equipment, weight, base $/mi, group)
_DOMINANT = [
    ("Chicago", "IL", "Atlanta", "GA", "van", 18.0, 2.30, "chi-atl"),
    ("New Orleans", "LA", "Memphis", "TN", "reefer", 14.0, 2.55, "nol-mem"),
    ("Columbus", "OH", "Charlotte", "NC", "van", 11.0, 2.45, "cmh-clt"),
    ("Nashville-Davidson", "TN", "Jacksonville", "FL", "reefer", 9.0, 2.60, "bna-jax"),
    ("Harrisburg", "PA", "Jacksonville", "FL", "flatbed", 8.0, 2.20, "hbg-jax"),
]
_RISING = ("Jacksonville", "FL", "Chicago", "IL", "reefer", 4.0, 2.20, "jax-chi")
_TAIL = [
    ("Indianapolis", "IN", "Detroit", "MI", "van", 1.6, 2.75),
    ("Louisville", "KY", "Columbus", "OH", "van", 1.6, 2.80),
    ("Atlanta", "GA", "Charlotte", "NC", "van", 1.6, 2.75),
    ("Memphis", "TN", "St. Louis", "MO", "reefer", 1.6, 2.85),
    ("Pittsburgh", "PA", "Buffalo", "NY", "flatbed", 1.6, 2.90),
    ("Newark", "NJ", "Boston", "MA", "van", 1.5, 2.95),
    ("Birmingham", "AL", "Atlanta", "GA", "van", 1.5, 2.85),
    ("Atlanta", "GA", "Chicago", "IL", "van", 1.75, 2.30),
    ("Philadelphia", "PA", "Atlanta", "GA", "van", 1.75, 2.35),
    ("New Orleans", "LA", "Birmingham", "AL", "reefer", 1.75, 2.45),
    ("St. Louis", "MO", "Nashville-Davidson", "TN", "flatbed", 1.75, 2.35),
    ("Detroit", "MI", "Charlotte", "NC", "van", 1.75, 2.30),
    ("Des Moines", "IA", "Chicago", "IL", "flatbed", 1.75, 2.35),
    ("Birmingham", "AL", "Memphis", "TN", "stepdeck", 1.75, 2.40),
    ("Shreveport", "LA", "Little Rock", "AR", "van", 1.75, 2.35),
    ("Newark", "NJ", "Miami", "FL", "van", 1.7, 2.05),
    ("Orlando", "FL", "Charlotte", "NC", "reefer", 1.7, 2.15),
    ("Minneapolis", "MN", "Chicago", "IL", "flatbed", 1.7, 2.10),
    ("Chicago", "IL", "Memphis", "TN", "van", 1.7, 2.10),
    ("Atlanta", "GA", "Louisville", "KY", "stepdeck", 1.7, 2.10),
    ("Tampa", "FL", "Atlanta", "GA", "reefer", 1.7, 2.15),
    ("Miami", "FL", "Chicago", "IL", "reefer", 1.7, 2.10),
]
# Documentation of what changed vs 0036/0037 (the old lane -> its in-area replacement).
_REPLACED = {
    "Dallas,TX>Memphis,TN": "New Orleans,LA>Memphis,TN",
    "Laredo,TX>Chicago,IL": "Jacksonville,FL>Chicago,IL",
    "Los Angeles,CA>Phoenix,AZ": "Nashville,TN>Jacksonville,FL",
    "Houston,TX>Dallas,TX": "Louisville,KY>Columbus,OH",
    "Phoenix,AZ>Las Vegas,NV": "Memphis,TN>St. Louis,MO",
    "Seattle,WA>Portland,OR": "Pittsburgh,PA>Buffalo,NY",
    "Atlanta,GA>Dallas,TX": "Atlanta,GA>Chicago,IL",
    "Houston,TX>Memphis,TN": "New Orleans,LA>Birmingham,AL",
    "St. Louis,MO>Dallas,TX": "St. Louis,MO>Nashville,TN",
    "Denver,CO>Omaha,NE": "Des Moines,IA>Chicago,IL",
    "Birmingham,AL>Dallas,TX": "Birmingham,AL>Memphis,TN",
    "Albuquerque,NM>Dallas,TX": "Shreveport,LA>Little Rock,AR",
    "Los Angeles,CA>Dallas,TX": "Orlando,FL>Charlotte,NC",
    "Seattle,WA>Denver,CO": "Minneapolis,MN>Chicago,IL",
    "Chicago,IL>Dallas,TX": "Chicago,IL>Memphis,TN",
    "Atlanta,GA>Denver,CO": "Atlanta,GA>Louisville,KY",
    "Phoenix,AZ>Houston,TX": "Tampa,FL>Atlanta,GA",
}

# age bucket (A newest 6 months, B months 6-12, C older) -> multiplier
_RATE = {
    "nol-mem": (0.838, 0.942, 1.0),
    "chi-atl": (1.122, 1.039, 1.0),
    "jax-chi": (1.152, 1.048, 1.0),
}
_VOLUME = {
    "nol-mem": (0.65, 0.80, 1.0),
    "chi-atl": (1.35, 1.15, 1.0),
    "jax-chi": (2.2, 1.3, 0.6),
}

# Demo trucks homed outside the area move here: (city, st) -> (city, st, lat, lng).
_NEW_HOMES = {
    ("Dallas", "TX"): ("Nashville-Davidson", "TN", 36.1718, -86.785),
    ("Phoenix", "AZ"): ("St. Louis", "MO", 38.6270, -90.1994),
}


def generate_runs(
    now: datetime,
    diesel_for: Callable[[str, date], float | None],
    resolve: Callable[[str, str], tuple[float, float]],
    highway_miles: Callable[[float, float, float, float], int],
) -> list[dict[str, Any]]:
    """Pure + deterministic: same ``now`` and lookups -> same rows. 0037's model, in-area lanes."""
    m = _M36
    rng = random.Random(20261009)
    lanes: list[dict[str, Any]] = []
    for oc, os_, dc, ds, eq, w, rpm, grp in [*_DOMINANT, _RISING]:
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
    for _ in range(m.TOTAL_RUNS):
        days_ago = rng.randrange(0, m.HISTORY_DAYS)
        day = today - timedelta(days=days_ago)
        b = _M37._bucket(days_ago)
        weights = []
        for ln in lanes:
            w = ln["w"] * m._season(ln["eq"], day.month)
            if ln["grp"] in _VOLUME:
                w *= _VOLUME[ln["grp"]][b]
            weights.append(w)
        ln = rng.choices(lanes, weights=weights, k=1)[0]

        miles = max(40, round(ln["base_miles"] * rng.uniform(0.97, 1.03)))
        rpm = ln["rpm"] * 0.88 * rng.uniform(0.94, 1.06)
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
            "tenant_id": LJM_TENANT_ID, "source": "demo", "broker_name": rng.choice(m._BROKERS),
            "broker_lead_id": None,
            "origin_city": ln["oc"], "origin_state": ln["os"], "dest_city": ln["dc"], "dest_state": ln["ds"],
            "origin_lat": ln["o_lat"], "origin_lng": ln["o_lng"], "dest_lat": ln["d_lat"], "dest_lng": ln["d_lng"],
            "miles": miles, "deadhead_miles": deadhead, "equipment": ln["eq"],
            "pickup_at": pickup, "delivery_at": delivery,
            "revenue_usd": revenue, "cost_fuel_usd": fuel, "cost_driver_usd": driver,
            "cost_load_usd": load, "cost_dispatch_usd": dispatch,
            "raw": {"demo": True, "lane_group": ln["grp"], "shift_arc": ("post", "post", "pre")[b], "age_bucket": "ABC"[b]},
        })
    rows.sort(key=lambda r: r["pickup_at"])
    return rows


def _reseed_and_relink(generate: Callable[..., list[dict[str, Any]]]) -> None:
    bind = op.get_bind()
    t = {"t": LJM_TENANT_ID}
    # Same delete / insert / diesel / resolve plumbing as 0037; only the generator differs.
    _M37._reseed(generate)
    bind.execute(sa.text("DELETE FROM lane_insights_cache WHERE tenant_id = :t"), t)  # (0037 already clears; harmless)

    trucks = [
        {"id": r[0], "equipment": r[1], "status": r[2], "kind": r[3], "home_base_city": r[4], "home_base_state": r[5]}
        for r in bind.execute(sa.text(
            "SELECT id, equipment, status, kind, home_base_city, home_base_state FROM trucks "
            "WHERE tenant_id = :t AND source = 'demo'"), t)
    ]
    if not trucks:
        return
    now = datetime.now(UTC)
    runs = [
        {"id": r[0], "equipment": r[1], "pickup_at": _M38._utc(r[2]), "delivery_at": _M38._utc(r[3]),
         "lat": float(r[4]), "lng": float(r[5])}
        for r in bind.execute(sa.text(
            "SELECT id, equipment, pickup_at, delivery_at, origin_lat, origin_lng FROM freight_runs "
            "WHERE tenant_id = :t AND source = 'demo' ORDER BY pickup_at, id"), t)
    ]
    plan = _M38.assign_runs(runs, [u for u in trucks if u["kind"] == "truck"], now)
    if plan:
        bind.execute(sa.text("UPDATE freight_runs SET truck_id = :truck_id WHERE id = :id"),
                     [{"id": rid, "truck_id": v["truck_id"]} for rid, v in plan.items()])

    # home bases outside the area move in; positions use 0038's parking rule with the extra homes known
    homes = {(c, s): (la, lo) for c, s, la, lo in _M38._HOMES}
    for c, s, la, lo in _NEW_HOMES.values():
        homes[(c, s)] = (la, lo)
    moves = []
    for u in trucks:
        new = _NEW_HOMES.get((u["home_base_city"], u["home_base_state"]))
        if new:
            u["home_base_city"], u["home_base_state"] = new[0], new[1]
            moves.append({"id": u["id"], "c": new[0], "s": new[1]})
    if moves:
        bind.execute(sa.text("UPDATE trucks SET home_base_city = :c, home_base_state = :s WHERE id = :id"), moves)
    saved = _M38._HOMES
    _M38._HOMES = [(c, s, la, lo) for (c, s), (la, lo) in homes.items()]
    try:
        placed = _M38.last_positions(trucks, runs, plan, now)
    finally:
        _M38._HOMES = saved
    pos = [{"id": tid, "lat": lat, "lng": lng, "seen": seen} for tid, (lat, lng, seen) in placed.items()]
    bind.execute(sa.text("UPDATE trucks SET last_lat = :lat, last_lng = :lng, last_seen_at = :seen WHERE id = :id"), pos)


def upgrade() -> None:
    _reseed_and_relink(generate_runs)


def downgrade() -> None:
    _reseed_and_relink(_M37.generate_runs)  # back to the 0037 data shape; trucks stay homed in-area
