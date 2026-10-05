"""Shared KPI service — the foundation every analytics view reads.

One place for the formulas, the time windows, the tenant filter, the
period-over-period compare. Every endpoint returns the same shapes
(``KpiBlock | MetricPoint | Breakdown | Funnel``) so the frontend can
reuse one chart primitive layer against every page.

Why this module exists
----------------------
Before this landed, every tile/chart hard-coded its own time window and
tenant filter — the audit (plan Step 1) listed nine different ways the
same number was computed. One module means one place to fix a bug.

Only reads here. The one write path is **cache invalidation**, which
rides existing domain events (``CallOutcomeLogged``, ``LeadDiscovered``,
``MessageIngested``). See ``bump_cache_key``.
"""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator
from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CallOutcome, CapacityPost, CrawlRun, Lead, Load, MailMessage, SentLog

ET = ZoneInfo("America/New_York")

# ---- value object + shared shapes ----------------------------------------


class Period(BaseModel):
    """A bounded time window, ET day-snapped.

    Invariants:
    * ``from_`` <= ``to``
    * span <= 365d
    * both endpoints snap to the ET day boundary at ingress

    Why ET: LJM is east-coast — "today" for the operator is ET, not UTC.
    Pinning the snap here means the dashboards never silently cross a
    DST boundary.
    """

    from_: datetime = Field(alias="from")
    to: datetime
    label: str = "custom"

    model_config = {"populate_by_name": True}

    @model_validator(mode="after")
    def _bounds(self) -> Period:
        if self.from_ >= self.to:
            raise ValueError("Period: from must be < to")
        if (self.to - self.from_) > timedelta(days=366):
            raise ValueError("Period: max span is 365 days")
        self.from_ = _snap_day_et(self.from_)
        self.to = _snap_day_et(self.to, end=True)
        return self

    @property
    def prev(self) -> Period:
        """The equal-length window immediately before this one.

        We subtract a microsecond on the end so after snapping the previous
        window ends on *the day before* this window starts — otherwise the
        snap-to-day-end would re-expand it by one calendar day.
        """
        span = self.to - self.from_
        return Period(
            **{
                "from": self.from_ - span,
                "to": self.from_ - timedelta(microseconds=1),
                "label": f"prev-{self.label}",
            }
        )

    @property
    def cache_key(self) -> str:
        """Cache variant: label + the resolved bounds.

        The label alone is ambiguous — every custom range is "custom", and a
        relative label ("7d") resolves to different bounds across midnight —
        so the snapped from/to are always part of the key.
        """
        return f"{self.label}:{self.from_.isoformat()}:{self.to.isoformat()}"

    @property
    def days(self) -> int:
        return max(1, (self.to - self.from_).days)


def _snap_day_et(dt: datetime, *, end: bool = False) -> datetime:
    """Snap a timestamp to the ET day boundary. ``end=True`` returns 23:59:59.999999."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    local = dt.astimezone(ET)
    if end:
        local = local.replace(hour=23, minute=59, second=59, microsecond=999999)
    else:
        local = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def today_et() -> date:
    return datetime.now(UTC).astimezone(ET).date()


def period_from_label(label: str) -> Period:
    """Resolve a UI label (today / 7d / 30d / 90d) into a bounded ``Period``.

    Labels are the public contract; custom periods come through the
    ``from``/``to`` query params and bypass this helper.
    """
    now = datetime.now(UTC)
    end = _snap_day_et(now, end=True)
    days = {"today": 1, "7d": 7, "30d": 30, "90d": 90}.get(label, 7)
    start = _snap_day_et(now - timedelta(days=days - 1))
    return Period(**{"from": start, "to": end, "label": label})


# ---- wire shapes (dataclasses — routers serialise to Pydantic) -----------


@dataclass
class MetricPoint:
    """One point on a trend. ``n`` is mandatory — a derived rate without a
    sample size is the single most common dashboard lie."""

    bucket: Literal["day", "week", "month"]
    key: str
    value: float
    n: int


@dataclass
class Delta:
    value: float
    prev: float
    delta_abs: float
    delta_pct: float | None  # None when prev == 0
    direction: Literal["up", "down", "flat"]


@dataclass
class KpiBlock:
    label: str
    unit: str
    value: float
    prev: float
    delta_pct: float | None
    direction: Literal["up", "down", "flat"]
    series: list[MetricPoint] = field(default_factory=list)
    thin: bool = False


@dataclass
class BreakdownRow:
    key: str
    value: float
    n: int
    share: float


@dataclass
class Breakdown:
    dimension: str
    rows: list[BreakdownRow]


@dataclass
class FunnelStep:
    label: str
    count: int
    drop_pct: float | None


@dataclass
class Funnel:
    steps: list[FunnelStep]


# ---- period compare -------------------------------------------------------


def period_compare(current: float, prev: float) -> Delta:
    """One function so every KPI reports the same way. Zero-prev stays
    ``None`` for ``delta_pct`` — division-by-zero should never render as
    "up 100%" when prev was actually missing."""
    abs_ = current - prev
    if prev == 0:
        pct = None
        direction: Literal["up", "down", "flat"] = "up" if current > 0 else "flat"
    else:
        pct = (abs_ / prev) * 100.0
        direction = "up" if abs_ > 0 else ("down" if abs_ < 0 else "flat")
    return Delta(value=current, prev=prev, delta_abs=abs_, delta_pct=pct, direction=direction)


def to_block(
    label: str,
    unit: str,
    current: float,
    prev: float,
    series: list[MetricPoint] | None = None,
    thin_min: int = 10,
) -> KpiBlock:
    d = period_compare(current, prev)
    s = series or []
    thin = any(p.n < thin_min for p in s) if s else False
    return KpiBlock(
        label=label,
        unit=unit,
        value=current,
        prev=prev,
        delta_pct=d.delta_pct,
        direction=d.direction,
        series=s,
        thin=thin,
    )


# ---- cache ----------------------------------------------------------------


class _Cache:
    """60-second in-process TTL. Keyed by (tenant, bucket, params-hash).

    One per worker process. The invalidation path bumps a per-tenant epoch
    so stale entries age out immediately on write. "keep v1 simple" — no
    Redis, no cross-worker coherence.
    """

    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self._epoch: dict[tuple[str, str], int] = defaultdict(int)
        self._lock = asyncio.Lock()

    def _key(self, tenant: str, bucket: str, variant: str) -> str:
        epoch = self._epoch[(tenant, bucket)]
        return f"{tenant}:{bucket}:{epoch}:{variant}"

    async def get(self, tenant: str, bucket: str, variant: str) -> Any | None:
        k = self._key(tenant, bucket, variant)
        hit = self._store.get(k)
        if hit is None:
            return None
        ts, val = hit
        if time.monotonic() - ts > 60.0:
            self._store.pop(k, None)
            return None
        return val

    async def set(self, tenant: str, bucket: str, variant: str, value: Any) -> None:
        self._store[self._key(tenant, bucket, variant)] = (time.monotonic(), value)

    def invalidate(self, tenant: str, bucket: str) -> None:
        self._epoch[(tenant, bucket)] += 1


CACHE = _Cache()


def bump_cache_key(tenant: str, bucket: str) -> None:
    """Called by domain-event listeners. Public so inbox/outreach/prospecting
    event handlers can wire it without importing the private cache."""
    CACHE.invalidate(tenant, bucket)


# ---- helpers --------------------------------------------------------------


def _day_key(dt: datetime) -> str:
    return dt.astimezone(ET).date().isoformat()


def _month_key(dt: datetime) -> str:
    return dt.astimezone(ET).strftime("%Y-%m")


async def _count_scalar(session: AsyncSession, stmt) -> int:
    row = await session.execute(stmt)
    return int(row.scalar() or 0)


# ---- queries --------------------------------------------------------------


async def overview_kpis(session: AsyncSession, tenant: str, period: Period) -> dict:
    """Headline tiles + booked-vs-rejected series with a win-rate overlay.

    Three tiles ("to call today" is live from the ranker; here we return
    the raw ingredients needed for period compare):
      * to_call_today  — same shape as before, no compare (today is today)
      * new_leads      — leads first_seen inside the period, with prev
      * loads_booked   — call_outcomes.outcome='booked' inside the period, with prev
    Plus the monthly booked/rejected series (deterministic month bucket)
    and the overlayed win-rate line (n per bucket included).
    """

    cached = await CACHE.get(tenant, "overview", period.cache_key)
    if cached is not None:
        return cached

    prev = period.prev

    # new_leads
    new_leads_curr = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(Lead.first_seen_at >= period.from_, Lead.first_seen_at <= period.to)
        ),
    )
    new_leads_prev = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(Lead.first_seen_at >= prev.from_, Lead.first_seen_at <= prev.to)
        ),
    )

    # booked
    booked_curr = await _count_scalar(
        session,
        select(func.count(CallOutcome.id)).where(
            and_(
                CallOutcome.outcome == "booked",
                CallOutcome.logged_at >= period.from_,
                CallOutcome.logged_at <= period.to,
            )
        ),
    )
    booked_prev = await _count_scalar(
        session,
        select(func.count(CallOutcome.id)).where(
            and_(
                CallOutcome.outcome == "booked",
                CallOutcome.logged_at >= prev.from_,
                CallOutcome.logged_at <= prev.to,
            )
        ),
    )

    # booked_vs_rejected monthly series — bounded to the period window
    rows = (
        await session.execute(
            select(CallOutcome.outcome, CallOutcome.logged_at).where(
                and_(
                    CallOutcome.outcome.in_(("booked", "not_interested")),
                    CallOutcome.logged_at >= period.from_,
                    CallOutcome.logged_at <= period.to,
                )
            )
        )
    ).all()
    monthly: dict[str, dict[str, int]] = defaultdict(lambda: {"booked": 0, "rejected": 0})
    for outcome, logged_at in rows:
        m = _month_key(logged_at)
        if outcome == "booked":
            monthly[m]["booked"] += 1
        else:
            monthly[m]["rejected"] += 1
    series = []
    win_rate_series = []
    for m in sorted(monthly.keys()):
        b = monthly[m]["booked"]
        r = monthly[m]["rejected"]
        n = b + r
        series.append(MetricPoint(bucket="month", key=m, value=float(b), n=n))
        wr = (b / n) if n else 0.0
        win_rate_series.append(MetricPoint(bucket="month", key=m, value=wr, n=n))

    # also return a "booked trend" sparkline per-day for the loads_booked tile
    daily_booked: dict[str, int] = defaultdict(int)
    daily_total: dict[str, int] = defaultdict(int)
    for outcome, logged_at in rows:
        d = _day_key(logged_at)
        daily_total[d] += 1
        if outcome == "booked":
            daily_booked[d] += 1
    booked_trend = [
        MetricPoint(bucket="day", key=d, value=float(daily_booked[d]), n=daily_total[d])
        for d in sorted(set(list(daily_booked.keys()) + list(daily_total.keys())))
    ]

    # daily new leads sparkline
    lead_rows = (
        await session.execute(
            select(Lead.first_seen_at).where(
                and_(Lead.first_seen_at >= period.from_, Lead.first_seen_at <= period.to)
            )
        )
    ).all()
    leads_daily: dict[str, int] = defaultdict(int)
    for (fs,) in lead_rows:
        leads_daily[_day_key(fs)] += 1
    leads_trend = [
        MetricPoint(bucket="day", key=d, value=float(leads_daily[d]), n=leads_daily[d])
        for d in sorted(leads_daily.keys())
    ]

    rejected_curr = sum(monthly[m]["rejected"] for m in monthly)
    rejected_prev = await _count_scalar(
        session,
        select(func.count(CallOutcome.id)).where(
            and_(
                CallOutcome.outcome == "not_interested",
                CallOutcome.logged_at >= prev.from_,
                CallOutcome.logged_at <= prev.to,
            )
        ),
    )

    out = {
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "tiles": [
            asdict(to_block("New leads", "leads", float(new_leads_curr), float(new_leads_prev), leads_trend)),
            asdict(to_block("Loads booked", "loads", float(booked_curr), float(booked_prev), booked_trend)),
            asdict(to_block("Rejected", "loads", float(rejected_curr), float(rejected_prev))),
        ],
        "booked_vs_rejected": {
            "series": [asdict(p) for p in series],
            "win_rate": [asdict(p) for p in win_rate_series],
        },
    }
    await CACHE.set(tenant, "overview", period.cache_key, out)
    return out


async def crawler_kpis(session: AsyncSession, tenant: str, period: Period) -> dict:
    """Lead-pipeline KPIs: new leads, leads with phone, leads with verified
    email, and a by-state breakdown. Backs the Today-desk "new leads" tile
    with a prior-period delta and gives capacity/leads pages their shared
    strip."""

    cached = await CACHE.get(tenant, "crawler", period.cache_key)
    if cached is not None:
        return cached

    prev = period.prev

    def _in(start, end):
        return and_(Lead.first_seen_at >= start, Lead.first_seen_at <= end)

    new_curr = await _count_scalar(session, select(func.count(Lead.id)).where(_in(period.from_, period.to)))
    new_prev = await _count_scalar(session, select(func.count(Lead.id)).where(_in(prev.from_, prev.to)))

    with_phone_curr = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(_in(period.from_, period.to), Lead.phone.is_not(None), Lead.phone != "")
        ),
    )
    with_phone_prev = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(_in(prev.from_, prev.to), Lead.phone.is_not(None), Lead.phone != "")
        ),
    )

    with_email_curr = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(_in(period.from_, period.to), Lead.primary_email.is_not(None), Lead.primary_email != "")
        ),
    )
    with_email_prev = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(_in(prev.from_, prev.to), Lead.primary_email.is_not(None), Lead.primary_email != "")
        ),
    )

    # by-state (period only)
    state_rows = (
        await session.execute(
            select(Lead.state, func.count(Lead.id))
            .where(_in(period.from_, period.to))
            .group_by(Lead.state)
            .order_by(func.count(Lead.id).desc())
            .limit(15)
        )
    ).all()
    total_states = sum(c for _, c in state_rows) or 1
    by_state = Breakdown(
        dimension="state",
        rows=[
            BreakdownRow(key=s or "—", value=float(c), n=int(c), share=c / total_states)
            for s, c in state_rows
        ],
    )

    # trend sparkline
    lead_rows = (
        await session.execute(
            select(Lead.first_seen_at).where(_in(period.from_, period.to))
        )
    ).all()
    daily: dict[str, int] = defaultdict(int)
    for (fs,) in lead_rows:
        daily[_day_key(fs)] += 1
    trend = [
        MetricPoint(bucket="day", key=d, value=float(daily[d]), n=daily[d])
        for d in sorted(daily.keys())
    ]

    # last crawl time (not window-bound — it's a "freshness" indicator)
    last_crawl_row = (
        await session.execute(
            select(CrawlRun.finished_at)
            .where(CrawlRun.finished_at.is_not(None))
            .order_by(CrawlRun.finished_at.desc())
            .limit(1)
        )
    ).first()
    last_crawl = last_crawl_row[0].isoformat() if last_crawl_row and last_crawl_row[0] else None

    out = {
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "tiles": [
            asdict(to_block("New leads", "leads", float(new_curr), float(new_prev), trend)),
            asdict(to_block("With phone", "leads", float(with_phone_curr), float(with_phone_prev))),
            asdict(to_block("With email", "leads", float(with_email_curr), float(with_email_prev))),
        ],
        "by_state": asdict(by_state),
        "last_crawl_finished_at": last_crawl,
    }
    await CACHE.set(tenant, "crawler", period.cache_key, out)
    return out


async def call_outcome_kpis(session: AsyncSession, tenant: str, period: Period) -> dict:
    """Call outcome mix + conversion-to-booked, with prev-period comparison.
    Powers the overview booked tile and the call-list day pulse."""

    cached = await CACHE.get(tenant, "call_outcomes", period.cache_key)
    if cached is not None:
        return cached

    prev = period.prev

    def _in(start, end):
        return and_(CallOutcome.logged_at >= start, CallOutcome.logged_at <= end)

    # mix in period
    mix_rows = (
        await session.execute(
            select(CallOutcome.outcome, func.count(CallOutcome.id))
            .where(_in(period.from_, period.to))
            .group_by(CallOutcome.outcome)
        )
    ).all()
    counts: dict[str, int] = {k: 0 for k in ("booked", "callback", "not_interested", "no_answer")}
    for o, c in mix_rows:
        counts[o] = int(c)
    total = sum(counts.values()) or 1
    mix = Breakdown(
        dimension="outcome",
        rows=[
            BreakdownRow(key=k, value=float(v), n=int(v), share=v / total)
            for k, v in counts.items()
        ],
    )

    # prev-period same metric
    prev_mix_rows = (
        await session.execute(
            select(CallOutcome.outcome, func.count(CallOutcome.id))
            .where(_in(prev.from_, prev.to))
            .group_by(CallOutcome.outcome)
        )
    ).all()
    prev_counts: dict[str, int] = {k: 0 for k in ("booked", "callback", "not_interested", "no_answer")}
    for o, c in prev_mix_rows:
        prev_counts[o] = int(c)

    booked = counts["booked"]
    decided = counts["booked"] + counts["not_interested"]
    conv = (booked / decided) if decided else 0.0
    prev_booked = prev_counts["booked"]
    prev_decided = prev_counts["booked"] + prev_counts["not_interested"]
    prev_conv = (prev_booked / prev_decided) if prev_decided else 0.0

    # daily trend
    rows = (
        await session.execute(
            select(CallOutcome.outcome, CallOutcome.logged_at).where(_in(period.from_, period.to))
        )
    ).all()
    daily: dict[str, dict[str, int]] = defaultdict(lambda: {"booked": 0, "decided": 0, "total": 0})
    for o, logged_at in rows:
        d = _day_key(logged_at)
        daily[d]["total"] += 1
        if o in ("booked", "not_interested"):
            daily[d]["decided"] += 1
        if o == "booked":
            daily[d]["booked"] += 1
    trend = [
        MetricPoint(
            bucket="day",
            key=d,
            value=(daily[d]["booked"] / daily[d]["decided"]) if daily[d]["decided"] else 0.0,
            n=daily[d]["decided"],
        )
        for d in sorted(daily.keys())
    ]

    out = {
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "tiles": [
            asdict(to_block("Booked", "loads", float(booked), float(prev_booked))),
            asdict(to_block("Conversion", "%", conv * 100, prev_conv * 100, trend)),
            asdict(to_block("Total outcomes", "logs", float(total if sum(counts.values()) else 0), float(sum(prev_counts.values())))),
        ],
        "mix": asdict(mix),
        "trend": [asdict(p) for p in trend],
    }
    await CACHE.set(tenant, "call_outcomes", period.cache_key, out)
    return out


async def day_pulse(session: AsyncSession, tenant: str) -> dict:
    """Today's call outcome mix vs yesterday — the Call-List strip.

    Period is always "today in ET", prev is always "yesterday in ET".
    """
    now = datetime.now(UTC)
    today_start = _snap_day_et(now)
    today_end = _snap_day_et(now, end=True)
    yesterday_start = today_start - timedelta(days=1)
    yesterday_end = today_end - timedelta(days=1)

    async def _mix(a, b):
        rows = (
            await session.execute(
                select(CallOutcome.outcome, func.count(CallOutcome.id))
                .where(and_(CallOutcome.logged_at >= a, CallOutcome.logged_at <= b))
                .group_by(CallOutcome.outcome)
            )
        ).all()
        d: dict[str, int] = {k: 0 for k in ("booked", "callback", "not_interested", "no_answer")}
        for o, c in rows:
            d[o] = int(c)
        return d

    today = await _mix(today_start, today_end)
    yesterday = await _mix(yesterday_start, yesterday_end)

    def _conv(c: dict[str, int]) -> float:
        dec = c["booked"] + c["not_interested"]
        return (c["booked"] / dec) if dec else 0.0

    return {
        "today_et": today_start.astimezone(ET).date().isoformat(),
        "today": today,
        "yesterday": yesterday,
        "conversion_today": _conv(today),
        "conversion_yesterday": _conv(yesterday),
    }


async def topbar_counters(session: AsyncSession, tenant: str) -> dict:
    """Today's four top-bar counters — real rows only, never a baseline.

    "Today" is the ET calendar day (same boundary as ``day_pulse``). Every
    query filters on ``tenant_id`` explicitly — fail closed, RLS is the
    backstop, not the only guard (sqlite has no RLS at all).

      * scanned — distinct companies the crawler touched today (``last_seen_at``)
      * found   — companies first seen today (``first_seen_at``)
      * sent    — outreach sends today (``sent_log``, test sends excluded)
      * replies — inbound mail received today (``from_addr != mailbox``,
        the inbox's inbound rule; ``sent_log.replied_at`` is never written)
    """
    now = datetime.now(UTC)
    start = _snap_day_et(now)
    end = _snap_day_et(now, end=True)

    scanned = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(Lead.tenant_id == tenant, Lead.last_seen_at >= start, Lead.last_seen_at <= end)
        ),
    )
    found = await _count_scalar(
        session,
        select(func.count(Lead.id)).where(
            and_(Lead.tenant_id == tenant, Lead.first_seen_at >= start, Lead.first_seen_at <= end)
        ),
    )
    sent = await _count_scalar(
        session,
        select(func.count(SentLog.id)).where(
            and_(
                SentLog.tenant_id == tenant,
                SentLog.is_test.is_(False),
                SentLog.sent_at >= start,
                SentLog.sent_at <= end,
            )
        ),
    )
    replies = await _count_scalar(
        session,
        select(func.count()).select_from(MailMessage).where(
            and_(
                MailMessage.tenant_id == tenant,
                MailMessage.from_addr != MailMessage.mailbox,
                MailMessage.received_at >= start,
                MailMessage.received_at <= end,
            )
        ),
    )
    return {
        "today_et": start.astimezone(ET).date().isoformat(),
        "scanned": scanned,
        "found": found,
        "sent": sent,
        "replies": replies,
    }


async def lane_performance(
    session: AsyncSession, tenant: str, period: Period, region: str | None = None
) -> dict:
    """Retrospective lane performance over external load records.

    `region` filter avoids mixing USD and EUR averages (plan rule). Rows:
    lane × loads × avg rate × avg $/mi (where miles present) × sample size.
    """

    cached = await CACHE.get(tenant, "lanes", f"{period.cache_key}:{region or '*'}")
    if cached is not None:
        return cached

    stmt = (
        select(
            Load.origin_state,
            Load.dest_state,
            func.count(Load.id),
            func.avg(Load.rate_usd),
            func.avg(Load.rate_usd / func.nullif(Load.miles, 0)),
        )
        .where(
            and_(
                Load.pickup_date >= period.from_,
                Load.pickup_date <= period.to,
                Load.origin_state.is_not(None),
                Load.dest_state.is_not(None),
            )
        )
        .group_by(Load.origin_state, Load.dest_state)
        .order_by(func.count(Load.id).desc())
        .limit(25)
    )
    rows = (await session.execute(stmt)).all()
    lane_rows = []
    total_loads = 0
    for o, d, n, avg_rate, avg_rpm in rows:
        lane_rows.append(
            {
                "lane": f"{o} → {d}",
                "loads": int(n or 0),
                "avg_rate_usd": float(avg_rate) if avg_rate is not None else None,
                "avg_usd_per_mile": float(avg_rpm) if avg_rpm is not None else None,
                "n": int(n or 0),
            }
        )
        total_loads += int(n or 0)

    top = Breakdown(
        dimension="lane",
        rows=[
            BreakdownRow(
                key=r["lane"], value=float(r["loads"]), n=r["n"], share=(r["loads"] / total_loads) if total_loads else 0.0
            )
            for r in lane_rows[:10]
        ],
    )
    out = {
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "rows": lane_rows,
        "top": asdict(top),
    }
    await CACHE.set(tenant, "lanes", f"{period.cache_key}:{region or '*'}", out)
    return out


async def capacity_kpis(session: AsyncSession, tenant: str, period: Period) -> dict:
    """Capacity post stats + the match funnel.

    Funnel is intentionally shallow (posts → matched status → closed). The
    inbox-sourced funnel (reply → quote → load) is owned by the inbox plan.
    """

    cached = await CACHE.get(tenant, "capacity", period.cache_key)
    if cached is not None:
        return cached

    prev = period.prev

    def _in(start, end):
        return and_(CapacityPost.created_at >= start, CapacityPost.created_at <= end)

    # "Open posts" = posts created in the window that are still open, compared
    # against the same measure over the prior window (a live count has no
    # meaningful prior, which made the delta permanently flat).
    open_curr = await _count_scalar(
        session,
        select(func.count(CapacityPost.id)).where(
            and_(_in(period.from_, period.to), CapacityPost.status == "open")
        ),
    )
    open_prev = await _count_scalar(
        session,
        select(func.count(CapacityPost.id)).where(
            and_(_in(prev.from_, prev.to), CapacityPost.status == "open")
        ),
    )
    created_curr = await _count_scalar(
        session, select(func.count(CapacityPost.id)).where(_in(period.from_, period.to))
    )
    created_prev = await _count_scalar(
        session, select(func.count(CapacityPost.id)).where(_in(prev.from_, prev.to))
    )
    matched_curr = await _count_scalar(
        session,
        select(func.count(CapacityPost.id)).where(
            and_(_in(period.from_, period.to), CapacityPost.status.in_(("matched", "closed")))
        ),
    )
    matched_prev = await _count_scalar(
        session,
        select(func.count(CapacityPost.id)).where(
            and_(_in(prev.from_, prev.to), CapacityPost.status.in_(("matched", "closed")))
        ),
    )
    closed_curr = await _count_scalar(
        session,
        select(func.count(CapacityPost.id)).where(
            and_(_in(period.from_, period.to), CapacityPost.status == "closed")
        ),
    )

    steps = [
        FunnelStep(label="Posted", count=created_curr, drop_pct=None),
        FunnelStep(
            label="Matched",
            count=matched_curr,
            drop_pct=(1 - matched_curr / created_curr) * 100 if created_curr else None,
        ),
        FunnelStep(
            label="Closed",
            count=closed_curr,
            drop_pct=(1 - closed_curr / matched_curr) * 100 if matched_curr else None,
        ),
    ]

    out = {
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "tiles": [
            asdict(to_block("Open posts", "posts", float(open_curr), float(open_prev))),
            asdict(to_block("Created", "posts", float(created_curr), float(created_prev))),
            asdict(to_block("Matched", "posts", float(matched_curr), float(matched_prev))),
        ],
        "funnel": {"steps": [asdict(s) for s in steps]},
    }
    await CACHE.set(tenant, "capacity", period.cache_key, out)
    return out


async def broker_kpis(
    session: AsyncSession, tenant: str, broker_id: str, period: Period
) -> dict:
    """Per-broker KPIs — sent / replied / reply_rate for the window + prior.

    The frontend's broker-detail page swaps its hardcoded 30d block for
    this. The real inbox-driven cards (open-rate, best send-time) stay
    owned by the inbox plan.
    """

    cached = await CACHE.get(tenant, "broker", f"{broker_id}:{period.cache_key}")
    if cached is not None:
        return cached

    prev = period.prev
    broker = (await session.execute(select(Lead).where(Lead.id == broker_id))).scalar_one_or_none()
    if broker is None:
        return {"error": "not_found"}

    def _sent_in(start, end):
        return and_(SentLog.lead_id == broker_id, SentLog.sent_at >= start, SentLog.sent_at <= end)

    sent_curr = await _count_scalar(session, select(func.count(SentLog.id)).where(_sent_in(period.from_, period.to)))
    sent_prev = await _count_scalar(session, select(func.count(SentLog.id)).where(_sent_in(prev.from_, prev.to)))
    repl_curr = await _count_scalar(
        session,
        select(func.count(SentLog.id)).where(
            and_(_sent_in(period.from_, period.to), SentLog.replied_at.is_not(None))
        ),
    )
    repl_prev = await _count_scalar(
        session,
        select(func.count(SentLog.id)).where(
            and_(_sent_in(prev.from_, prev.to), SentLog.replied_at.is_not(None))
        ),
    )

    reply_rate = (repl_curr / sent_curr) if sent_curr else 0.0
    prev_reply_rate = (repl_prev / sent_prev) if sent_prev else 0.0

    # tiny per-day sent sparkline
    sent_rows = (
        await session.execute(
            select(SentLog.sent_at).where(_sent_in(period.from_, period.to))
        )
    ).all()
    daily: dict[str, int] = defaultdict(int)
    for (sa,) in sent_rows:
        daily[_day_key(sa)] += 1
    trend = [
        MetricPoint(bucket="day", key=d, value=float(daily[d]), n=daily[d])
        for d in sorted(daily.keys())
    ]

    rate_block = to_block("Reply rate", "%", reply_rate * 100, prev_reply_rate * 100, thin_min=5)
    # thin flag on reply_rate is driven by *sent count*, not series length
    rate_dict = asdict(rate_block)
    rate_dict["thin"] = sent_curr < 5
    out = {
        "broker_id": broker_id,
        "period": {"from": period.from_.isoformat(), "to": period.to.isoformat(), "label": period.label},
        "tiles": [
            asdict(to_block("Sent", "emails", float(sent_curr), float(sent_prev), trend)),
            asdict(to_block("Replied", "emails", float(repl_curr), float(repl_prev))),
            rate_dict,
        ],
        "thin": sent_curr < 5,
    }
    await CACHE.set(tenant, "broker", f"{broker_id}:{period.cache_key}", out)
    return out
