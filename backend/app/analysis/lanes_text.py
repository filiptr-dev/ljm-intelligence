"""Deterministic plain-language statements for the lanes page (amendment A1).

Pure functions over already-computed aggregates: no I/O, no AI. The page
shows these next to every chart so it reads correctly even with no Gemini key.
"""

from __future__ import annotations

from app.analysis.schemas import (
    CostShare,
    LaneMetrics,
    LaneRef,
    LaneStatement,
    LaneTrend,
    LengthBand,
)

MIN_RUNS_FOR_TREND = 3
LOW_SAMPLE_RUNS = 20  # below this in either half a trend is flagged "small sample"


def money(v: float) -> str:
    a = abs(v)
    sign = "-" if v < 0 else ""
    if a >= 1_000_000:
        return f"{sign}${a / 1_000_000:.2f}M"
    if a >= 10_000:
        return f"{sign}${a / 1_000:.0f}k"
    return f"{sign}${a:,.0f}"


def num(v: float) -> str:
    return f"{v:,.0f}"


def pct_change(cur: float, base: float) -> float | None:
    return None if not base else (cur - base) / base * 100.0


def signed(p: float, digits: int = 1) -> str:
    return f"{p:+.{digits}f}%"


def make_metrics(runs: int, miles: float, revenue: float, fuel: float, driver: float, load: float,
                 dispatch: float) -> LaneMetrics:
    cost = fuel + driver + load + dispatch
    margin = revenue - cost
    return LaneMetrics(
        runs=int(runs),
        miles=int(miles),
        revenue=round(revenue, 2),
        cost_fuel=round(fuel, 2),
        cost_driver=round(driver, 2),
        cost_load=round(load, 2),
        cost_dispatch=round(dispatch, 2),
        cost_total=round(cost, 2),
        margin=round(margin, 2),
        margin_pct=round(margin / revenue * 100, 1) if revenue else None,
        rate_per_mile=round(revenue / miles, 3) if miles else None,
        cost_per_mile=round(cost / miles, 3) if miles else None,
    )


def make_trend(label: str, recent: LaneMetrics, base: LaneMetrics) -> LaneTrend:
    """Recent half vs the half before it. None when either side is too thin."""
    counts = {
        "runs_recent": recent.runs, "runs_base": base.runs,
        "low_sample": recent.runs < LOW_SAMPLE_RUNS or base.runs < LOW_SAMPLE_RUNS,
    }
    if recent.runs < MIN_RUNS_FOR_TREND or base.runs < MIN_RUNS_FOR_TREND:
        return LaneTrend(label=label, **counts)
    rate = pct_change(recent.rate_per_mile or 0.0, base.rate_per_mile or 0.0)
    runs = pct_change(recent.runs, base.runs)
    rev = pct_change(recent.revenue, base.revenue)
    mpp = (
        round(recent.margin_pct - base.margin_pct, 1)
        if recent.margin_pct is not None and base.margin_pct is not None
        else None
    )
    return LaneTrend(
        label=label,
        runs_pct=None if runs is None else round(runs, 1),
        revenue_pct=None if rev is None else round(rev, 1),
        rate_pct=None if rate is None else round(rate, 1),
        margin_pp=mpp,
        **counts,
    )


def cost_shares(m: LaneMetrics) -> list[CostShare]:
    total = m.cost_total or 0.0
    rows = [
        ("fuel", "Fuel", m.cost_fuel),
        ("driver", "Driver pay", m.cost_driver),
        ("load", "Load costs (tolls, lumper, detention, insurance)", m.cost_load),
        ("dispatch", "Dispatch", m.cost_dispatch),
    ]
    return [
        CostShare(key=k, label=lbl, amount=round(a, 2), pct=round(a / total * 100, 1) if total else 0.0)  # type: ignore[arg-type]
        for k, lbl, a in rows
    ]


def entity_text(label: str, m: LaneMetrics, band: str | None, trend: LaneTrend, window_label: str,
                *, as_origin: int | None = None, as_dest: int | None = None) -> str:
    if m.runs == 0:
        return f"No runs for {label} in the {window_label}."
    parts = [
        f"{label}: {num(m.runs)} runs in the {window_label}, {num(m.miles)} miles, "
        f"{money(m.revenue)} revenue"
        + (f" at ${m.rate_per_mile:.2f}/mile." if m.rate_per_mile else ".")
    ]
    if as_origin is not None and as_dest is not None:
        parts.append(f"{num(as_origin)} started here and {num(as_dest)} ended here.")
    if m.cost_total:
        fuel_pct = m.cost_fuel / m.cost_total * 100
        parts.append(
            f"Fuel is {fuel_pct:.0f}% of cost; margin is {money(m.margin)}"
            + (f" ({m.margin_pct:.0f}%)." if m.margin_pct is not None else ".")
        )
    if band:
        parts.append(f"Most runs are {band} miles.")
    if trend.rate_pct is not None and trend.runs_pct is not None:
        direction = "up" if trend.rate_pct >= 0 else "down"
        vol = "more" if trend.runs_pct >= 0 else "fewer"
        parts.append(
            f"Rate per mile is {direction} {abs(trend.rate_pct):.1f}% with {abs(trend.runs_pct):.0f}% {vol} runs "
            f"({trend.label})."
        )
    return " ".join(parts)


def _ref_line(r: LaneRef) -> str:
    return f"{r.label} ({num(r.runs)} runs, {num(r.miles)} miles, {money(r.revenue)})"


def build_statements(
    *,
    window_label: str,
    window_days: int,
    kpis: LaneMetrics,
    recent: LaneMetrics,
    base: LaneMetrics,
    trend: LaneTrend,
    busiest_bucket: tuple[str, int] | None,
    shares: list[CostShare],
    bands: list[LengthBand],
    frequent: LaneRef | None,
    miles_lane: LaneRef | None,
    revenue_lane: LaneRef | None,
    shifts: list[str],
    shifting: bool,
) -> list[LaneStatement]:
    if kpis.runs == 0:
        return [LaneStatement(key="empty", title="No runs yet",
                              text="There are no completed runs in this window yet, so there is nothing to analyse.")]
    out: list[LaneStatement] = []
    weeks = max(window_days / 7.0, 1.0)
    months = max(window_days / 30.4, 1.0)
    years = max(window_days / 365.0, 1.0 / 12)
    t = (
        f"In the {window_label} you ran {num(kpis.runs)} runs covering {num(kpis.miles)} miles and "
        f"{money(kpis.revenue)} of revenue. That is about {kpis.runs / weeks:.0f} runs a week, "
        f"{kpis.runs / months:.0f} a month and {kpis.runs / years:.0f} a year at this pace."
    )
    if busiest_bucket:
        t += f" The busiest period was {busiest_bucket[0]} with {num(busiest_bucket[1])} runs."
    if trend.runs_pct is not None:
        more = "more" if trend.runs_pct >= 0 else "fewer"
        t += f" Volume is {abs(trend.runs_pct):.0f}% {more} than before ({trend.label})."
    out.append(LaneStatement(key="totals", title="Totals", text=t))

    if kpis.cost_total:
        big = max(shares, key=lambda s: s.pct)
        names = {"fuel": "fuel", "driver": "driver pay", "load": "load costs", "dispatch": "dispatch"}
        parts = ", ".join(f"{names[s.key]} {money(s.amount)} ({s.pct:.0f}%)" for s in shares)
        out.append(LaneStatement(
            key="cost_split", title="Where the money goes",
            text=(
                f"Your costs were {money(kpis.cost_total)}: {parts}. {names[big.key].capitalize()} is the biggest "
                f"line. Gross margin after these costs is {money(kpis.margin)}"
                + (f" ({kpis.margin_pct:.0f}% of revenue)." if kpis.margin_pct is not None else ".")
            ),
        ))
    if frequent:
        share = frequent.runs / kpis.runs * 100
        out.append(LaneStatement(
            key="most_frequent", title="Most frequent route",
            text=f"The route you run most often is {_ref_line(frequent)} — {share:.0f}% of all runs.",
        ))
    if miles_lane and revenue_lane:
        if miles_lane.key == revenue_lane.key:
            txt = (f"{_ref_line(miles_lane)} is the route with the most miles driven and also the most revenue "
                   f"({money(miles_lane.revenue)}).")
        else:
            txt = (f"The route with the most miles driven is {_ref_line(miles_lane)}; the route that earns the most "
                   f"is {_ref_line(revenue_lane)}.")
        out.append(LaneStatement(key="most_miles", title="Routes that run the most", text=txt))
    if bands:
        top = max(bands, key=lambda b: b.runs)
        out.append(LaneStatement(
            key="length_band", title="Typical trip length",
            text=f"Most of your runs ({top.pct:.0f}%) are {top.band} miles long — {num(top.runs)} runs "
                 f"and {money(top.revenue)} of revenue.",
        ))
    if shifting and shifts:
        txt = "Yes, the market looks like it is shifting. " + " ".join(shifts)
    elif shifts:
        txt = "No big shift, but note: " + " ".join(shifts)
    else:
        txt = ("No clear market shift in this window: rates per mile and volumes on your main lanes are within a "
               "few percent of the period before.")
    out.append(LaneStatement(key="shift", title="Is the market shifting?", text=txt))
    return out
