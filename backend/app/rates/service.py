"""Rates — service orchestration.

Composes domain + repository. No SQL here, no HTTP here. The three public
coroutines are the three tools; backhaul calls `quote_lane` on the way back
so the "expected $/mi" column is the same number the Lane page shows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.rates import domain, repository
from app.rates.schemas import (
    BackhaulCandidateOut,
    BackhaulIn,
    BackhaulOut,
    CityStateIn,
    CompRowOut,
    DieselRowOut,
    ProfitIn,
    ProfitOut,
    RateQuoteIn,
    RateQuoteOut,
)

# Comps window — a year of recent sends is enough to cover seasonality
# without pulling in rates that have since drifted. One place to tune.
COMPS_LOOKBACK_DAYS = 365
STALE_DIESEL_DAYS = 14


async def _resolve_diesel(session: AsyncSession, dest_state: str) -> tuple[DieselRowOut | None, bool]:
    """Return (row, stale). The lane uses the destination PADD because that's
    where the fuel is burned on the leg being quoted."""
    padd = domain.padd_for(dest_state)
    fallback = padd not in domain.STATE_TO_PADD.values() or dest_state.upper() not in domain.STATE_TO_PADD
    row = await repository.latest_padd_diesel(session, padd)
    if row is None:
        return None, False
    stale = (datetime.now(UTC).date() - row.week_of) > timedelta(days=STALE_DIESEL_DAYS)
    return (
        DieselRowOut(
            padd=row.padd,
            week_of=row.week_of.isoformat(),
            price_usd=float(row.price_usd),
            stale=stale,
            fallback_padd=fallback,
        ),
        stale,
    )


def _fuel_only_mid_per_mile(diesel: float, mpg: float, margin: float = 0.20) -> float:
    """When comps are empty, give the UI a plausible band center = fuel
    break-even + a small margin cushion (20%). The band widens around it in
    `domain.band_from_comps`."""
    fuel_per_mi = diesel / mpg
    return round(fuel_per_mi * (1.0 + margin), 2)


async def quote_lane(session: AsyncSession, body: RateQuoteIn) -> RateQuoteOut:
    origin_pt = domain.resolve_city(domain.CityState(city=body.origin.city, state=body.origin.state))
    dest_pt = domain.resolve_city(domain.CityState(city=body.dest.city, state=body.dest.state))
    lane = domain.Lane(origin=origin_pt, dest=dest_pt)
    gc = lane.great_circle_miles
    hw = lane.highway_miles

    diesel, diesel_stale = await _resolve_diesel(session, body.dest.state)
    diesel_price = diesel.price_usd if diesel else 0.0

    equipment = None if body.equipment == "ANY" else body.equipment
    comps_raw = await repository.recent_booked_rates(
        session,
        origin_state=body.origin.state,
        dest_state=body.dest.state,
        equipment=equipment,
        since=datetime.now(UTC) - timedelta(days=COMPS_LOOKBACK_DAYS),
    )
    comp_rates_per_mile = [c["rate_per_mile"] for c in comps_raw if c["rate_per_mile"] > 0]

    # Fallback midpoint — fuel-only break-even with a 20% cushion. If we have
    # no diesel row at all, we still give something: a conservative $2.00/mi
    # placeholder, badged.
    fallback_mid = _fuel_only_mid_per_mile(diesel_price, domain.DEFAULT_MPG) if diesel_price else 2.00
    band = domain.band_from_comps(comp_rates_per_mile, fallback_mid=fallback_mid)

    evidence: list[str] = []
    evidence.append(f"Great-circle miles: {gc:.0f} · highway factor {domain.HIGHWAY_FACTOR} → {hw} mi")
    if diesel is None:
        evidence.append("Diesel: not configured — add an EIA API key in Settings.")
    else:
        tag = " (stale)" if diesel_stale else ""
        fb = " (fallback PADD)" if diesel.fallback_padd else ""
        evidence.append(f"Diesel PADD {diesel.padd}{fb}, week of {diesel.week_of}: ${diesel.price_usd:.3f}/gal{tag}")
    if comps_raw:
        evidence.append(f"Comps: {len(comps_raw)} historical booking(s) on this lane (last {COMPS_LOOKBACK_DAYS}d)")
    else:
        evidence.append("Comps: none on file — band is fuel+margin only. Treat as a floor.")

    return RateQuoteOut(
        origin=body.origin,
        dest=body.dest,
        equipment=body.equipment,
        great_circle_miles=round(gc),
        highway_miles=hw,
        highway_factor=domain.HIGHWAY_FACTOR,
        diesel=diesel,
        rate_band={"low": band.low, "mid": band.mid, "high": band.high},
        rate_band_dollars={
            "low": round(band.low * hw, 0),
            "mid": round(band.mid * hw, 0),
            "high": round(band.high * hw, 0),
        },
        comps=[CompRowOut(**c) for c in comps_raw],
        comps_count=len(comps_raw),
        evidence=evidence,
    )


async def score_profit(session: AsyncSession, body: ProfitIn) -> ProfitOut:
    diesel, stale = await _resolve_diesel(session, body.dest_state)
    diesel_price = diesel.price_usd if diesel else 0.0
    if diesel_price <= 0:
        # Nothing cached, no key — don't 500; fall back to a conservative
        # national mean ($3.80/gal) so the user still gets a breakdown, and
        # badge it stale so they know.
        diesel_price = 3.80
        stale = True

    br = domain.score_profit(
        rate=body.rate_usd,
        loaded_miles=body.loaded_miles,
        deadhead_miles=body.deadhead_miles,
        diesel_per_gal=diesel_price,
        mpg=body.mpg,
        origin_state=body.origin_state,
        dest_state=body.dest_state,
    )
    evidence = [
        f"Fuel: {body.loaded_miles + body.deadhead_miles} mi ÷ {body.mpg} mpg × ${diesel_price:.3f}/gal",
        f"Driver: {body.loaded_miles} mi × ${domain.DRIVER_PAY_PER_LOADED_MILE}/mi"
        + (f" + {body.deadhead_miles} mi × ${domain.DRIVER_PAY_PER_DEADHEAD_MILE}/mi deadhead" if body.deadhead_miles else ""),
    ]
    if br.toll_corridor:
        evidence.append(f"Toll corridor: {br.toll_corridor} (~${br.toll_cost:.0f})")
    else:
        evidence.append("No known toll corridor for this state pair — $0 tolls.")
    if stale:
        evidence.append("Diesel row is stale (>14 days or unconfigured) — margin treats fuel as a rough floor.")
    evidence.append(
        f"Verdict thresholds: green ≥ {domain.VERDICT_GREEN_PCT}%, tight ≥ {domain.VERDICT_TIGHT_PCT}%, red < {domain.VERDICT_TIGHT_PCT}%"
    )

    return ProfitOut(
        rate_usd=br.rate,
        loaded_miles=br.loaded_miles,
        deadhead_miles=br.deadhead_miles,
        mpg=br.mpg,
        diesel_per_gal=br.diesel_per_gal,
        diesel_stale=stale,
        fuel_cost=br.fuel_cost,
        driver_cost=br.driver_cost,
        toll_cost=br.toll_cost,
        toll_corridor=br.toll_corridor,
        deadhead_cost=br.deadhead_cost,
        total_cost=br.total_cost,
        net=br.net,
        margin_pct=br.margin_pct,
        verdict=br.verdict,
        verdict_thresholds={"green": domain.VERDICT_GREEN_PCT, "tight": domain.VERDICT_TIGHT_PCT},
        evidence=evidence,
    )


@dataclass
class _ScoredLead:
    row: dict
    distance_mi: int
    toward_home: bool
    headed_home_score: int


async def find_backhauls(session: AsyncSession, body: BackhaulIn) -> BackhaulOut:
    drop_pt = domain.resolve_city(domain.CityState(city=body.drop.city, state=body.drop.state))
    rows = await repository.backhaul_candidates(session, equipment=None)

    # Score every row that resolves to a city centroid and sits inside the
    # radius. v1 does a Python haversine after a cheap state-based prune —
    # 50k leads tops in this app, well under the budget.
    scored: list[_ScoredLead] = []
    home = (body.home_state or "").upper()
    for r in rows:
        if not r["city"] or not r["state"]:
            continue
        try:
            pt = domain.resolve_city(domain.CityState(city=r["city"], state=r["state"]))
        except domain.LaneResolveError:
            # City not in the centroid file — skip the row rather than fake a
            # coordinate. The user sees fewer rows, never wrong ones.
            continue
        d = domain.haversine_miles(drop_pt.lat, drop_pt.lon, pt.lat, pt.lon)
        if d > body.radius_miles:
            continue
        # Toward-home heuristic: lead's state is closer to home than the drop
        # city's state. We proxy "closer" by comparing distance to a home
        # state centroid (first known city in that state) when we have one.
        toward = False
        home_bonus = 0
        try:
            # Approximate home centroid by any known city in the home state.
            home_pt = next(
                domain.resolve_city(domain.CityState(city=c["city"], state=home))
                for c in domain._cities()
                if c["state"].upper() == home
            )
            d_from_drop_to_home = domain.haversine_miles(
                drop_pt.lat, drop_pt.lon, home_pt.lat, home_pt.lon
            )
            d_from_lead_to_home = domain.haversine_miles(
                pt.lat, pt.lon, home_pt.lat, home_pt.lon
            )
            toward = d_from_lead_to_home < d_from_drop_to_home
            if toward:
                home_bonus = round((d_from_drop_to_home - d_from_lead_to_home) / 50)
        except (StopIteration, domain.LaneResolveError):
            pass
        contact_score = (1 if r["phone"] else 0) + (1 if r["email"] else 0)
        fit = (r["fit_score"] or r["current_score"] or 0) // 10
        score = home_bonus * 2 + contact_score * 5 + fit
        scored.append(_ScoredLead(row=r, distance_mi=round(d), toward_home=toward, headed_home_score=score))

    # Last-contacted join
    last_touched = await repository.last_contacted_at(session, [s.row["id"] for s in scored])

    scored.sort(key=lambda s: (-s.headed_home_score, s.distance_mi))

    # Expected $/mi: reuse `quote_lane`'s band mid for each lead (lane =
    # drop → lead). This is the knob that unifies the three tools.
    candidates: list[BackhaulCandidateOut] = []
    for s in scored[:50]:
        expected = None
        try:
            q = await quote_lane(
                session,
                RateQuoteIn(
                    origin=CityStateIn(city=drop_pt.city, state=drop_pt.state),
                    dest=CityStateIn(city=s.row["city"], state=s.row["state"]),
                    equipment=body.equipment,
                ),
            )
            expected = q.rate_band["mid"]
        except Exception:  # noqa: BLE001  - a bad per-lane quote mustn't 500 the backhaul list
            expected = None
        last_at = last_touched.get(s.row["id"])
        candidates.append(
            BackhaulCandidateOut(
                lead_id=s.row["id"],
                name=s.row["name"],
                kind=s.row["kind"],
                city=s.row["city"],
                state=s.row["state"],
                distance_miles=s.distance_mi,
                toward_home=s.toward_home,
                headed_home_score=s.headed_home_score,
                expected_rate_per_mile=expected,
                has_phone=bool(s.row["phone"]),
                has_email=bool(s.row["email"]),
                last_contacted_at=last_at.isoformat() if last_at else None,
                phone=s.row["phone"],
                email=s.row["email"],
            )
        )

    evidence = [
        f"Searched {len(rows)} contactable leads; {len(scored)} inside {body.radius_miles} mi of {drop_pt.city}, {drop_pt.state}.",
        "Score = toward-home bonus × 2 + contact-fitness × 5 + fit/10. Ties broken by distance.",
    ]
    return BackhaulOut(
        drop=body.drop,
        home_state=body.home_state,
        equipment=body.equipment,
        radius_miles=body.radius_miles,
        candidates=candidates,
        evidence=evidence,
    )
