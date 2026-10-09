"""Rates — SQL seam.

Three functions, three questions. Service depends on these, not on
`select(...)` call sites. All reads; no writes except the EIA refresh job
(it goes through `upsert_diesel_prices`).
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.outreach.models import SentLog
from app.prospecting.models import Lead, Load
from app.rates.models import DieselPrice


async def latest_padd_diesel(session: AsyncSession, padd: str) -> DieselPrice | None:
    """Most recent diesel row for a PADD, or None if the table is empty."""
    row = await session.execute(
        select(DieselPrice)
        .where(DieselPrice.padd == padd)
        .order_by(DieselPrice.week_of.desc())
        .limit(1)
    )
    return row.scalar_one_or_none()


async def recent_booked_rates(
    session: AsyncSession,
    *,
    origin_state: str,
    dest_state: str,
    equipment: str | None,
    since: datetime | None = None,
    limit: int = 20,
) -> list[dict]:
    """Booked rates for the same lane, newest first.

    v1 reads `loads` directly (where `rate_usd` is set) joined to `sent_log`
    via `broker_lead_id → lead_id`. A `sent_log` row proves outreach; the
    joined `loads` row carries the lane + rate. Rows missing a rate or
    missing a lane are filtered out — comps must be real.
    """
    stmt = (
        select(
            Load.rate_usd,
            Load.miles,
            Load.origin_city,
            Load.origin_state,
            Load.dest_city,
            Load.dest_state,
            Load.equipment,
            Load.pickup_date,
        )
        .join(SentLog, SentLog.lead_id == Load.broker_lead_id)
        .where(
            Load.origin_state == origin_state.upper(),
            Load.dest_state == dest_state.upper(),
            Load.rate_usd.is_not(None),
            Load.miles.is_not(None),
            Load.miles > 0,
        )
        .order_by(SentLog.sent_at.desc())
        .limit(limit)
    )
    if equipment:
        stmt = stmt.where(Load.equipment == equipment)
    if since is not None:
        stmt = stmt.where(SentLog.sent_at >= since)
    rows = (await session.execute(stmt)).all()
    out: list[dict] = []
    for r in rows:
        rate = float(r.rate_usd)
        miles = int(r.miles)
        out.append({
            "rate_usd": rate,
            "miles": miles,
            "rate_per_mile": round(rate / miles, 2) if miles else 0.0,
            "origin": f"{r.origin_city or ''}, {r.origin_state or ''}".strip(", "),
            "dest": f"{r.dest_city or ''}, {r.dest_state or ''}".strip(", "),
            "equipment": r.equipment,
            "pickup_date": r.pickup_date.isoformat() if r.pickup_date else None,
        })
    return out


async def backhaul_candidates(
    session: AsyncSession,
    *,
    equipment: str | None,
    bbox: tuple[float, float, float, float] | None = None,
    limit: int = 200,
) -> list[dict]:
    """Return broker/shipper leads with contact fitness, filtered by state
    equipment overlap. v1 skips the geographic bounding-box prune at the
    SQL layer (city centroid lives in a static file, not the DB) — the
    service filters by distance in Python after pulling leads with a phone
    OR email.
    """
    stmt = (
        select(
            Lead.id,
            Lead.name,
            Lead.kind,
            Lead.state,
            Lead.city,
            Lead.phone,
            Lead.primary_email,
            Lead.current_score,
            Lead.fit_score,
            Lead.lane_origin_region,
            Lead.lane_destination_region,
        )
        .where(
            or_(Lead.phone.is_not(None), Lead.primary_email.is_not(None)),
            Lead.state.is_not(None),
            Lead.city.is_not(None),
        )
        .limit(limit)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "id": r.id,
            "name": r.name,
            "kind": r.kind,
            "state": r.state,
            "city": r.city,
            "phone": r.phone,
            "email": r.primary_email,
            "current_score": r.current_score,
            "fit_score": r.fit_score,
            "lane_origin_region": r.lane_origin_region,
            "lane_destination_region": r.lane_destination_region,
        }
        for r in rows
    ]


async def last_contacted_at(session: AsyncSession, lead_ids: list[str]) -> dict[str, datetime]:
    """Last sent_at per lead_id. Empty dict for leads never contacted."""
    if not lead_ids:
        return {}
    rows = (
        await session.execute(
            select(SentLog.lead_id, func.max(SentLog.sent_at))
            .where(SentLog.lead_id.in_(lead_ids))
            .group_by(SentLog.lead_id)
        )
    ).all()
    return {lid: ts for lid, ts in rows if ts is not None}


async def upsert_diesel_prices(
    session: AsyncSession, rows: list[tuple[str, date, Decimal]]
) -> int:
    """Idempotent upsert on (padd, week_of). Called by the weekly refresh job
    (and by tests). Returns the number of rows attempted; a repeat tick is a
    no-op on identical content."""
    if not rows:
        return 0
    bind = session.get_bind()
    dialect = bind.dialect.name if bind is not None else "postgresql"
    if dialect == "postgresql":
        stmt = pg_insert(DieselPrice.__table__).values([
            {"padd": p, "week_of": w, "price_usd": price} for p, w, price in rows
        ])
        stmt = stmt.on_conflict_do_update(
            index_elements=["padd", "week_of"],
            set_={"price_usd": stmt.excluded.price_usd, "fetched_at": func.now()},
        )
        await session.execute(stmt)
    else:
        # SQLite path for unit tests — delete + insert per key.
        for p, w, price in rows:
            await session.execute(
                DieselPrice.__table__.delete().where(
                    and_(DieselPrice.padd == p, DieselPrice.week_of == w)
                )
            )
            await session.execute(
                DieselPrice.__table__.insert().values(
                    padd=p, week_of=w, price_usd=price
                )
            )
    await session.commit()
    return len(rows)
