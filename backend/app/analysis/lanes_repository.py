"""Lanes history — SQL seam over ``freight_runs`` / ``lane_insights_cache``.

All query-builder use for the lanes feature lives here (import-linter keeps
services off ``select``/``func``). Everything is a GROUP BY / SUM against the
ORM models — no row loops over the runs. Functions take a ``Scope`` (tenant +
optional state / lane filter) and a half-open ``[start, end)`` pickup window,
and return plain dicts so the services stay free of SQLAlchemy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

from sqlalchemy import Date, case, cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.models import FreightRun as R, LaneInsightsCache

AGG_NAMES = ("runs", "miles", "revenue", "fuel", "driver", "load", "dispatch")
BAND_COUNT = 5


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    state: str | None = None
    lane: str | None = None  # "Chicago,IL>Atlanta,GA" (city lane) or "IL>GA" (state lane)


def _city_state(s: str) -> tuple[str, str] | None:
    city, _, st = s.rpartition(",")
    return (city.strip(), st.strip().upper()) if city and st else None


def _clauses(scope: Scope, start: datetime | None = None, end: datetime | None = None) -> list[Any]:
    cl: list[Any] = [R.tenant_id == scope.tenant_id]
    if scope.state:
        st = scope.state.upper()
        cl.append(or_(R.origin_state == st, R.dest_state == st))
    if scope.lane and ">" in scope.lane:
        o, _, d = scope.lane.partition(">")
        oc, dc = _city_state(o), _city_state(d)
        if oc and dc:
            cl += [R.origin_city == oc[0], R.origin_state == oc[1], R.dest_city == dc[0], R.dest_state == dc[1]]
        elif "," not in o and "," not in d:
            cl += [R.origin_state == o.strip().upper(), R.dest_state == d.strip().upper()]
    if start is not None:
        cl.append(R.pickup_at >= start)
    if end is not None:
        cl.append(R.pickup_at < end)
    return cl


_AGG = (
    func.count().label("runs"),
    func.coalesce(func.sum(R.miles), 0).label("miles"),
    func.coalesce(func.sum(R.revenue_usd), 0).label("revenue"),
    func.coalesce(func.sum(R.cost_fuel_usd), 0).label("fuel"),
    func.coalesce(func.sum(R.cost_driver_usd), 0).label("driver"),
    func.coalesce(func.sum(R.cost_load_usd), 0).label("load"),
    func.coalesce(func.sum(R.cost_dispatch_usd), 0).label("dispatch"),
)

# Named grouping keys, so callers never touch columns.
_BY: dict[str, tuple[Any, ...]] = {
    "total": (),
    "lane": (R.origin_city, R.origin_state, R.dest_city, R.dest_state),
    "state_lane": (R.origin_state, R.dest_state),
    "origin_state": (R.origin_state,),
    "dest_state": (R.dest_state,),
    "origin_city": (R.origin_city, R.origin_state),
    "dest_city": (R.dest_city, R.dest_state),
}


def _f(v: Any) -> float:
    return float(v or 0)


def _agg_dict(row: Any, offset: int) -> dict[str, float]:
    return {n: _f(row[offset + i]) for i, n in enumerate(AGG_NAMES)}


async def group(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime, by: str, *, intra_state: bool = False
) -> dict[tuple, dict[str, float]]:
    """Sums per group. ``intra_state`` keeps only runs that start and end in one state."""
    keys = _BY[by]
    cl = _clauses(scope, start, end)
    if intra_state:
        cl.append(R.origin_state == R.dest_state)
    stmt = select(*keys, *_AGG).where(*cl)
    if keys:
        stmt = stmt.group_by(*keys)
    rows = (await session.execute(stmt)).all()
    return {tuple(r[: len(keys)]): _agg_dict(r, len(keys)) for r in rows}


def _band_idx():
    return case((R.miles < 250, 0), (R.miles < 500, 1), (R.miles < 750, 2), (R.miles < 1000, 3), else_=4)


async def band_counts(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime, by: str
) -> dict[tuple, dict[int, int]]:
    keys = _BY[by]
    band = _band_idx().label("band")
    rows = (await session.execute(
        select(*keys, band, func.count()).where(*_clauses(scope, start, end)).group_by(*keys, band)
    )).all()
    out: dict[tuple, dict[int, int]] = {}
    for r in rows:
        out.setdefault(tuple(r[: len(keys)]), {})[int(r[len(keys)])] = int(r[len(keys) + 1])
    return out


async def band_revenue(session: AsyncSession, scope: Scope, start: datetime, end: datetime) -> dict[int, float]:
    band = _band_idx().label("band")
    rows = (await session.execute(
        select(band, func.coalesce(func.sum(R.revenue_usd), 0)).where(*_clauses(scope, start, end)).group_by(band)
    )).all()
    return {int(b): _f(v) for b, v in rows}


def _bucket_expr(dialect: str, period: str):
    if dialect == "postgresql":
        return cast(func.date_trunc(period, R.pickup_at), Date)
    if period == "week":
        return func.date(R.pickup_at, "weekday 0", "-6 days")  # Monday of the week
    if period == "month":
        return func.strftime("%Y-%m-01", R.pickup_at)
    return func.strftime("%Y-01-01", R.pickup_at)


async def buckets(
    session: AsyncSession, scope: Scope, period: str, start: datetime, end: datetime
) -> list[tuple[date, dict[str, float]]]:
    expr = _bucket_expr(session.get_bind().dialect.name, period).label("bucket")
    rows = (await session.execute(
        select(expr, *_AGG).where(*_clauses(scope, start, end)).group_by(expr).order_by(expr)
    )).all()
    return [(r[0] if isinstance(r[0], date) else date.fromisoformat(str(r[0])[:10]), _agg_dict(r, 1)) for r in rows]


async def history(session: AsyncSession, tenant_id: str) -> tuple[int, datetime | None, datetime | None]:
    r = (await session.execute(
        select(func.count(), func.min(R.pickup_at), func.max(R.pickup_at)).where(R.tenant_id == tenant_id)
    )).one()
    return int(r[0] or 0), r[1], r[2]


async def cities(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime
) -> dict[tuple[str, str], dict[str, Any]]:
    """(city, state) -> {lat, lng, o: sums-as-origin|None, d: sums-as-dest|None}."""
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for side, (c, s, lat, lng) in (
        ("o", (R.origin_city, R.origin_state, R.origin_lat, R.origin_lng)),
        ("d", (R.dest_city, R.dest_state, R.dest_lat, R.dest_lng)),
    ):
        rows = (await session.execute(
            select(c, s, func.max(lat), func.max(lng), *_AGG).where(*_clauses(scope, start, end)).group_by(c, s)
        )).all()
        for r in rows:
            e = out.setdefault((r[0], r[1]), {"lat": _f(r[2]), "lng": _f(r[3]), "o": None, "d": None})
            e[side] = _agg_dict(r, 4)
    return out


async def lane_coords(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime
) -> list[tuple[tuple, tuple[float, float, float, float], dict[str, float]]]:
    """(lane key, (o_lat, o_lng, d_lat, d_lng), sums) per city lane."""
    keys = _BY["lane"]
    rows = (await session.execute(
        select(*keys, func.max(R.origin_lat), func.max(R.origin_lng), func.max(R.dest_lat), func.max(R.dest_lng),
               *_AGG).where(*_clauses(scope, start, end)).group_by(*keys)
    )).all()
    return [(tuple(r[:4]), (_f(r[4]), _f(r[5]), _f(r[6]), _f(r[7])), _agg_dict(r, 8)) for r in rows]


async def count_runs(session: AsyncSession, scope: Scope, start: datetime, end: datetime) -> int:
    return int((await session.execute(select(func.count()).where(*_clauses(scope, start, end)))).scalar() or 0)


async def runs_before(
    session: AsyncSession, scope: Scope, start: datetime, end: datetime,
    after: tuple[datetime, int] | None, limit: int,
) -> Sequence[R]:
    """Newest first; keyset on (pickup_at, id) strictly after ``after``."""
    cl = _clauses(scope, start, end)
    if after:
        cl.append(or_(R.pickup_at < after[0], (R.pickup_at == after[0]) & (R.id < after[1])))
    return (await session.execute(
        select(R).where(*cl).order_by(R.pickup_at.desc(), R.id.desc()).limit(limit)
    )).scalars().all()


async def get_cache(session: AsyncSession, tenant_id: str, period_key: str) -> LaneInsightsCache | None:
    return (await session.execute(
        select(LaneInsightsCache).where(
            LaneInsightsCache.tenant_id == tenant_id, LaneInsightsCache.period_key == period_key
        )
    )).scalar_one_or_none()


def add_cache(session: AsyncSession, **fields: Any) -> None:
    session.add(LaneInsightsCache(**fields))
