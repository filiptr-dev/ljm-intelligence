"""Lanes history — assembles the page payloads from ``lanes_repository`` aggregates.

Three windows are in play:

* the *display window* D (week = 13 weeks, month = 12 months, year = 24
  months) — KPIs, charts, tables and the map all read it;
* the *recent half* (last D/2) and the *baseline half* (the D/2 before) — the
  only comparison that works on every period, used for every trend arrow and
  the market-shift call.

Filters (a state, or one lane) apply to everything except nothing: the page
simply re-requests with them. The map is requested unfiltered so the viewer
can click somewhere else.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis import lanes_repository as repo, lanes_text as T
from app.analysis.lanes_repository import AGG_NAMES, Scope
from app.analysis.schemas import (
    CityEntity,
    HeatArc,
    HeatmapData,
    HeatPoint,
    LaneBucket,
    LaneEntity,
    LaneMetrics,
    LaneRef,
    LanesPeriod,
    LanesSummary,
    LengthBand,
    RunRow,
    RunsPage,
    TopLane,
    TopLanes,
)

__all__ = ["Scope", "heatmap", "lane_key", "make_window", "runs_page", "summary", "top_lanes"]

WINDOW_DAYS: dict[str, int] = {"week": 91, "month": 365, "year": 730}
WINDOW_LABEL = {"week": "last 13 weeks", "month": "last 12 months", "year": "last 24 months"}
HALF_LABEL = {
    "week": "last 6 weeks vs the 6 weeks before",
    "month": "last 6 months vs the 6 months before",
    "year": "last 12 months vs the 12 months before",
}
BAND_LABELS = ["0–250", "250–500", "500–750", "750–1000", "1000+"]
MAX_ARCS = 50
MAX_POINTS = 1000
MAX_CITY_ENTITIES = 150
SHIFT_MIN_RUNS = 8
SHIFT_RATE_PCT = 4.0
SHIFT_VOLUME_PCT = 20.0

Agg = dict[str, float]


# ---- windows ----------------------------------------------------------------


@dataclass(frozen=True)
class Window:
    period: str
    start: datetime
    mid: datetime
    base_start: datetime
    end: datetime

    @property
    def days(self) -> int:
        return WINDOW_DAYS[self.period]


def make_window(period: str, now: datetime | None = None) -> Window:
    now = now or datetime.now(UTC)
    days = WINDOW_DAYS[period]
    half = days // 2
    return Window(period, now - timedelta(days=days), now - timedelta(days=half),
                  now - timedelta(days=2 * half), now)


# ---- small helpers ----------------------------------------------------------


def _metrics(d: Agg | None) -> LaneMetrics:
    d = d or dict.fromkeys(AGG_NAMES, 0.0)
    return T.make_metrics(d["runs"], d["miles"], d["revenue"], d["fuel"], d["driver"], d["load"], d["dispatch"])


def _merge(*ds: Agg | None) -> Agg:
    out = dict.fromkeys(AGG_NAMES, 0.0)
    for d in ds:
        for k, v in (d or {}).items():
            out[k] += v
    return out


def _sum_bands(*ds: dict[int, int] | None) -> dict[int, int]:
    out: dict[int, int] = {}
    for d in ds:
        for k, v in (d or {}).items():
            out[k] = out.get(k, 0) + v
    return out


def _dominant(bands: dict[int, int] | None) -> str | None:
    return BAND_LABELS[max(bands, key=lambda k: bands[k])] if bands else None


def _bucket_label(period: str, d: date) -> str:
    if period == "week":
        return d.strftime("%b %-d")
    if period == "month":
        return d.strftime("%b '%y")
    return str(d.year)


def lane_key(oc: str, os_: str, dc: str, ds: str) -> str:
    return f"{oc},{os_}>{dc},{ds}"


def _lane_label(oc: str, os_: str, dc: str, ds: str) -> str:
    return f"{oc}, {os_} → {dc}, {ds}"


def _lane_ref(k: tuple, d: Agg) -> LaneRef:
    return LaneRef(
        key=lane_key(*k), label=_lane_label(*k), runs=int(d["runs"]), miles=int(d["miles"]),
        revenue=round(d["revenue"], 2),
        rate_per_mile=round(d["revenue"] / d["miles"], 3) if d["miles"] else None,
    )


def detect_shifts(recent: dict[tuple, Agg], base: dict[tuple, Agg]) -> tuple[list[str], bool]:
    """Compare lanes between the two halves. Returns (sentences, is_shifting)."""
    found: list[tuple[float, str]] = []
    for k, r in recent.items():
        b = base.get(k)
        name = _lane_label(*k)
        if (b is None or b["runs"] == 0) and r["runs"] >= SHIFT_MIN_RUNS:
            found.append((r["runs"], f"{name} is a new lane: {int(r['runs'])} runs and none before."))
            continue
        if not b or r["runs"] < SHIFT_MIN_RUNS or b["runs"] < SHIFT_MIN_RUNS:
            continue
        rate = T.pct_change(r["revenue"] / r["miles"], b["revenue"] / b["miles"]) if r["miles"] and b["miles"] else None
        vol = T.pct_change(r["runs"], b["runs"])
        bits = []
        if rate is not None and abs(rate) >= SHIFT_RATE_PCT:
            bits.append(f"rate per mile {T.signed(rate)}")
        if vol is not None and abs(vol) >= SHIFT_VOLUME_PCT:
            bits.append(f"volume {T.signed(vol, 0)}")
        if bits:
            found.append((abs(rate or 0) + abs(vol or 0) / 3, f"{name}: {' and '.join(bits)}."))
    found.sort(key=lambda x: -x[0])
    return [s for _, s in found[:4]], bool(found)


# ---- summary ----------------------------------------------------------------


async def summary(
    session: AsyncSession, scope: Scope, period: LanesPeriod, now: datetime | None = None
) -> LanesSummary:
    w = make_window(period, now)
    buckets = [
        LaneBucket(key=d.isoformat(), label=_bucket_label(period, d), metrics=_metrics(agg))
        for d, agg in await repo.buckets(session, scope, period, w.start, w.end)
    ]
    tot = (await repo.group(session, scope, w.start, w.end, "total")).get(())
    rec = (await repo.group(session, scope, w.mid, w.end, "total")).get(())
    bas = (await repo.group(session, scope, w.base_start, w.mid, "total")).get(())
    kpis, recent_m, base_m = _metrics(tot), _metrics(rec), _metrics(bas)
    trend = T.make_trend(HALF_LABEL[period], recent_m, base_m)

    band_runs = (await repo.band_counts(session, scope, w.start, w.end, "total")).get((), {})
    band_rev = await repo.band_revenue(session, scope, w.start, w.end)
    total_runs = sum(band_runs.values()) or 1
    bands = [
        LengthBand(band=BAND_LABELS[i], runs=band_runs.get(i, 0), pct=round(band_runs.get(i, 0) / total_runs * 100, 1),
                   revenue=round(band_rev.get(i, 0.0), 2))
        for i in range(len(BAND_LABELS))
    ]

    lanes_cur = await repo.group(session, scope, w.start, w.end, "lane")
    lanes_rec = await repo.group(session, scope, w.mid, w.end, "lane")
    lanes_base = await repo.group(session, scope, w.base_start, w.mid, "lane")
    frequent = miles_lane = revenue_lane = None
    if lanes_cur:
        frequent = _lane_ref(*max(lanes_cur.items(), key=lambda kv: kv[1]["runs"]))
        miles_lane = _lane_ref(*max(lanes_cur.items(), key=lambda kv: kv[1]["miles"]))
        revenue_lane = _lane_ref(*max(lanes_cur.items(), key=lambda kv: kv[1]["revenue"]))
    shifts, shifting = detect_shifts(lanes_rec, lanes_base)

    history_runs, first, last = await repo.history(session, scope.tenant_id)
    months = max(1, round((last - first).days / 30.4)) if first and last else 0

    busiest = None
    if buckets:
        b = max(buckets, key=lambda x: x.metrics.runs)
        busiest = (b.label, b.metrics.runs)
    shares = T.cost_shares(kpis)
    return LanesSummary(
        period=period, window_label=WINDOW_LABEL[period], window_start=w.start.date().isoformat(),
        window_end=w.end.date().isoformat(), history_runs=history_runs, history_months=months,
        buckets=buckets, kpis=kpis, trend=trend, cost_split=shares, length_bands=bands,
        most_frequent_lane=frequent, most_miles_lane=miles_lane, most_revenue_lane=revenue_lane,
        shifting=shifting,
        statements=T.build_statements(
            window_label=WINDOW_LABEL[period], window_days=w.days, kpis=kpis, recent=recent_m, base=base_m,
            trend=trend, busiest_bucket=busiest, shares=shares, bands=bands, frequent=frequent,
            miles_lane=miles_lane, revenue_lane=revenue_lane, shifts=shifts, shifting=shifting,
        ),
    )


# ---- top lanes --------------------------------------------------------------


async def top_lanes(
    session: AsyncSession, scope: Scope, period: LanesPeriod, level: str = "state", limit: int = 20,
    now: datetime | None = None,
) -> TopLanes:
    w = make_window(period, now)
    by = "lane" if level == "city" else "state_lane"
    cur = await repo.group(session, scope, w.start, w.end, by)
    rec = await repo.group(session, scope, w.mid, w.end, by)
    bas = await repo.group(session, scope, w.base_start, w.mid, by)
    out: list[TopLane] = []
    for k, d in sorted(cur.items(), key=lambda kv: (-kv[1]["runs"], -kv[1]["revenue"]))[:limit]:
        tr = T.make_trend("", _metrics(rec.get(k)), _metrics(bas.get(k)))
        m = _metrics(d)
        if level == "city":
            key, o, de = lane_key(*k), f"{k[0]}, {k[1]}", f"{k[2]}, {k[3]}"
        else:
            key, o, de = f"{k[0]}>{k[1]}", k[0], k[1]
        out.append(TopLane(key=key, origin=o, dest=de, runs=m.runs, miles=m.miles, revenue=m.revenue,
                           avg_rate_per_mi=m.rate_per_mile, margin_pct=m.margin_pct, trend_pct=tr.rate_pct,
                           runs_trend_pct=tr.runs_pct, runs_recent=tr.runs_recent, runs_base=tr.runs_base,
                           low_sample=tr.low_sample))
    return TopLanes(level="city" if level == "city" else "state", window_label=WINDOW_LABEL[period], lanes=out)


# ---- heat map ---------------------------------------------------------------


async def _state_table(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime
) -> dict[str, tuple[Agg, int, int]]:
    """state -> (sums over runs touching it, runs as origin, runs as dest)."""
    o = await repo.group(session, scope, start, end, "origin_state")
    d = await repo.group(session, scope, start, end, "dest_state")
    intra = await repo.group(session, scope, start, end, "origin_state", intra_state=True)
    out: dict[str, tuple[Agg, int, int]] = {}
    for st in {*(k[0] for k in o), *(k[0] for k in d)}:
        a, b, c = o.get((st,)), d.get((st,)), intra.get((st,))
        merged = _merge(a, b)
        if c:  # a run inside one state was counted as both origin and dest
            merged = {k: merged[k] - c[k] for k in merged}
        out[st] = (merged, int((a or {}).get("runs", 0)), int((b or {}).get("runs", 0)))
    return out


def _st_agg(table: dict[str, tuple[Agg, int, int]], st: str) -> Agg | None:
    hit = table.get(st)
    return hit[0] if hit else None


async def heatmap(
    session: AsyncSession, scope: Scope, period: LanesPeriod, now: datetime | None = None
) -> HeatmapData:
    w = make_window(period, now)
    wl, hl = WINDOW_LABEL[period], HALF_LABEL[period]
    win = (w.start, w.end)
    rec_win = (w.mid, w.end)
    bas_win = (w.base_start, w.mid)

    # states
    st_cur = await _state_table(session, scope, *win)
    st_rec = await _state_table(session, scope, *rec_win)
    st_bas = await _state_table(session, scope, *bas_win)
    st_bands_o = await repo.band_counts(session, scope, *win, "origin_state")
    st_bands_d = await repo.band_counts(session, scope, *win, "dest_state")
    states: list[LaneEntity] = []
    for st, (agg, ro, rd) in sorted(st_cur.items(), key=lambda kv: -kv[1][0]["runs"]):
        m = _metrics(agg)
        tr = T.make_trend(hl, _metrics(_st_agg(st_rec, st)), _metrics(_st_agg(st_bas, st)))
        bnd = _dominant(_sum_bands(st_bands_o.get((st,)), st_bands_d.get((st,))))
        states.append(LaneEntity(
            key=f"state:{st}", kind="state", label=st, metrics=m, dominant_band=bnd, trend=tr,
            runs_as_origin=ro, runs_as_dest=rd,
            text=T.entity_text(st, m, bnd, tr, wl, as_origin=ro, as_dest=rd),
        ))

    # cities: heat points + pickable city entities
    ct_cur = await repo.cities(session, scope, *win)
    ct_rec = await repo.cities(session, scope, *rec_win)
    ct_bas = await repo.cities(session, scope, *bas_win)
    ct_bands_o = await repo.band_counts(session, scope, *win, "origin_city")
    ct_bands_d = await repo.band_counts(session, scope, *win, "dest_city")
    origins: list[HeatPoint] = []
    dests: list[HeatPoint] = []
    city_rows: list[tuple[tuple[str, str], dict[str, Any], Agg]] = []
    for k, e in ct_cur.items():
        city_rows.append((k, e, _merge(e["o"], e["d"])))
        key = f"city:{k[0]},{k[1]}"
        for side, bucket in (("o", origins), ("d", dests)):
            a = e[side]
            if a and a["runs"]:
                bucket.append(HeatPoint(lat=e["lat"], lng=e["lng"], weight=round(a["revenue"], 2),
                                        runs=int(a["runs"]), revenue=round(a["revenue"], 2), key=key))
    origins = sorted(origins, key=lambda p: -p.weight)[:MAX_POINTS]
    dests = sorted(dests, key=lambda p: -p.weight)[:MAX_POINTS]
    cities: list[CityEntity] = []
    for k, e, agg in sorted(city_rows, key=lambda x: -x[2]["runs"])[:MAX_CITY_ENTITIES]:
        m = _metrics(agg)
        rec_e, bas_e = ct_rec.get(k), ct_bas.get(k)
        tr = T.make_trend(
            hl,
            _metrics(_merge(rec_e["o"], rec_e["d"]) if rec_e else None),
            _metrics(_merge(bas_e["o"], bas_e["d"]) if bas_e else None),
        )
        bnd = _dominant(_sum_bands(ct_bands_o.get(k), ct_bands_d.get(k)))
        ro, rd = int((e["o"] or {}).get("runs", 0)), int((e["d"] or {}).get("runs", 0))
        label = f"{k[0]}, {k[1]}"
        cities.append(CityEntity(
            key=f"city:{k[0]},{k[1]}", kind="city", label=label, metrics=m, dominant_band=bnd, trend=tr,
            runs_as_origin=ro, runs_as_dest=rd, lat=e["lat"], lng=e["lng"],
            text=T.entity_text(label, m, bnd, tr, wl, as_origin=ro, as_dest=rd),
        ))

    # arcs: top 50 city lanes by runs
    coords = await repo.lane_coords(session, scope, *win)
    ln_rec = await repo.group(session, scope, *rec_win, "lane")
    ln_bas = await repo.group(session, scope, *bas_win, "lane")
    ln_bands = await repo.band_counts(session, scope, *win, "lane")
    arcs: list[HeatArc] = []
    for k, (o_lat, o_lng, d_lat, d_lng), agg in sorted(coords, key=lambda c: (-c[2]["runs"], -c[2]["revenue"]))[:MAX_ARCS]:
        m = _metrics(agg)
        tr = T.make_trend(hl, _metrics(ln_rec.get(k)), _metrics(ln_bas.get(k)))
        bnd = _dominant(ln_bands.get(k))
        label = _lane_label(*k)
        ent = LaneEntity(key=f"lane:{lane_key(*k)}", kind="lane", label=label, metrics=m, dominant_band=bnd,
                         trend=tr, text=T.entity_text(label, m, bnd, tr, wl))
        arcs.append(HeatArc(
            key=lane_key(*k), o_lat=o_lat, o_lng=o_lng, d_lat=d_lat, d_lng=d_lng,
            o_label=f"{k[0]}, {k[1]}", d_label=f"{k[2]}, {k[3]}", runs=m.runs, trend_pct=tr.rate_pct, entity=ent,
        ))
    return HeatmapData(period=period, window_label=wl, origins=origins, dests=dests, cities=cities,
                       states=states, arcs=arcs)


# ---- runs table (keyset) ----------------------------------------------------


def _enc(pickup: datetime, rid: int) -> str:
    return base64.urlsafe_b64encode(f"{pickup.isoformat()}|{rid}".encode()).decode()


def _dec(cursor: str) -> tuple[datetime, int] | None:
    try:
        iso, _, rid = base64.urlsafe_b64decode(cursor.encode()).decode().partition("|")
        return datetime.fromisoformat(iso), int(rid)
    except (ValueError, UnicodeDecodeError):
        return None


async def runs_page(
    session: AsyncSession, scope: Scope, period: LanesPeriod, cursor: str | None = None, limit: int = 50,
    now: datetime | None = None,
) -> RunsPage:
    w = make_window(period, now)
    total = await repo.count_runs(session, scope, w.start, w.end)
    rows = list(await repo.runs_before(session, scope, w.start, w.end, _dec(cursor) if cursor else None, limit + 1))
    more = len(rows) > limit
    rows = rows[:limit]
    items = []
    for r in rows:
        rev = float(r.revenue_usd)
        parts = [float(x) for x in (r.cost_fuel_usd, r.cost_driver_usd, r.cost_load_usd, r.cost_dispatch_usd)]
        margin = rev - sum(parts)
        items.append(RunRow(
            id=r.id, pickup_at=r.pickup_at.isoformat(), origin=f"{r.origin_city}, {r.origin_state}",
            dest=f"{r.dest_city}, {r.dest_state}", equipment=r.equipment, miles=r.miles, revenue=round(rev, 2),
            cost_fuel=parts[0], cost_driver=parts[1], cost_load=parts[2], cost_dispatch=parts[3],
            margin=round(margin, 2), margin_pct=round(margin / rev * 100, 1) if rev else None,
        ))
    nxt = _enc(rows[-1].pickup_at, rows[-1].id) if more and rows else None
    return RunsPage(items=items, next_cursor=nxt, total=total)
