"""Overview service — ``GET /overview/today`` aggregate.

Thin business layer behind ``app/api/overview.py``. Composes the Today-desk
response from four reads:

* ``call_list_service.load_and_rank`` for the ranked call list
* the latest completed ``CrawlRun`` + "new leads since" window
* ``CallOutcome`` counts for the booked-vs-rejected chart and 90d tile
* open truck posts (for the capacity_match preview) via a per-post best-match

Composition — tiles + top-10 do-next merge + 90-day chart series — stays in
one place so the shape stays shallow and the ordering stays deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import func, select

from app.outreach.models import CallOutcome, CapacityPost
from app.prospecting.broker_rank_service import count_brokers_by_action
from app.prospecting.call_list_service import load_and_rank as _load_and_rank
from app.prospecting.models import CrawlRun, Lead
from app.prospecting.pipeline.match import score_broker_for_post

ET = ZoneInfo("America/New_York")

# Deterministic merge weights for ``do_next[]`` — kept here so changing them
# is a one-line review.
_PRIORITY = {"call": 3, "capacity_match": 2, "new_lead": 1}
_DO_NEXT_LIMIT = 10
_CALL_TOP_N = 5
_NEW_LEADS_PREVIEW = 3
_CAPACITY_MATCH_LIMIT = 3
_LOADS_BOOKED_DEMO_FLOOR = 10


# ---------- dataclasses -----------------------------------------------------


@dataclass
class TileCountRow:
    value: int
    href: str


@dataclass
class NewLeadsTileRow:
    value: int
    since: str | None
    href: str


@dataclass
class LoadsBookedTileRow:
    value: int
    demo: bool
    href: str


@dataclass
class TilesRow:
    to_call_today: TileCountRow
    new_leads_since_last_crawl: NewLeadsTileRow
    loads_booked_90d: LoadsBookedTileRow


@dataclass
class DoNextCallRow:
    kind: Literal["call"]
    lead_id: str
    name: str
    state: str
    phone: str
    score: int
    reason: str
    href: str = "/call-list"


@dataclass
class DoNextCapacityMatchRow:
    kind: Literal["capacity_match"]
    post_id: str
    equipment: str
    origin_state: str
    lead_id: str
    lead_name: str
    reason: str
    href: str = "/capacity"


@dataclass
class DoNextNewLeadRow:
    kind: Literal["new_lead"]
    lead_id: str
    name: str
    state: str
    kind_label: str
    first_seen_at: str
    href: str = "/leads"


@dataclass
class MonthPointRow:
    month: str
    booked: int
    rejected: int


@dataclass
class BookedVsRejectedRow:
    demo: bool
    series: list[MonthPointRow] = field(default_factory=list)


@dataclass
class OverviewTodayResult:
    date: str
    tiles: TilesRow
    do_next: list[DoNextCallRow | DoNextCapacityMatchRow | DoNextNewLeadRow]
    booked_vs_rejected: BookedVsRejectedRow


# ---------- helpers ---------------------------------------------------------


def today_et() -> date:
    """Today in America/New_York. Isolated so a test can patch it."""
    return datetime.now(tz=ET).date()


def _et_start_of_day(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=ET)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------- service entry point --------------------------------------------


async def get_today(sessionmaker: Any) -> OverviewTodayResult:
    today = today_et()
    et_midnight = _et_start_of_day(today)

    full_call_rows = await _load_and_rank(sessionmaker, today, 100)
    call_rows = full_call_rows[:_CALL_TOP_N]

    # Tile value shares one definition with the ``/brokers?next_action=call``
    # page the operator clicks through to: "brokers whose next-action chip
    # says Call" (per plan decision 2). ``count_brokers_by_action`` runs one
    # scalar SQL — no row materialization — so the tile stays well under the
    # /overview p50 budget even at 25k leads.
    to_call_today_count = await count_brokers_by_action(
        sessionmaker, kind="call"
    )

    async with sessionmaker() as s:
        last_crawl_at = (
            await s.execute(select(func.max(CrawlRun.finished_at)).where(CrawlRun.status == "done"))
        ).scalar_one_or_none()

        # New-leads tile. Normalise the threshold to UTC — SQLite stores
        # timezone-aware columns as ISO strings and lexicographic comparison
        # across mixed offsets would be wrong at the character level. Comparing
        # in UTC closes the trap for sqlite and is a no-op for Postgres.
        if last_crawl_at is not None:
            new_leads_threshold = last_crawl_at
            if new_leads_threshold.tzinfo is not None:
                new_leads_threshold = new_leads_threshold.astimezone(UTC)
            new_leads_since = _iso(last_crawl_at)
        else:
            new_leads_threshold = et_midnight.astimezone(UTC)
            new_leads_since = None  # UI reads this as "no crawl yet"

        new_leads_count = (
            await s.execute(
                select(func.count(Lead.id)).where(Lead.first_seen_at >= new_leads_threshold)
            )
        ).scalar_one()

        new_leads_rows = (
            (
                await s.execute(
                    select(Lead)
                    .where(Lead.first_seen_at >= new_leads_threshold)
                    .order_by(Lead.first_seen_at.desc())
                    .limit(_NEW_LEADS_PREVIEW)
                )
            )
            .scalars()
            .all()
        )

        booked_cutoff_dt = (datetime.now(tz=ET) - timedelta(days=90)).astimezone(UTC)
        booked_90d = (
            await s.execute(
                select(func.count(CallOutcome.id))
                .where(CallOutcome.outcome == "booked")
                .where(CallOutcome.logged_at >= booked_cutoff_dt)
            )
        ).scalar_one()

        outcomes_90d = (
            (
                await s.execute(
                    select(CallOutcome)
                    .where(CallOutcome.logged_at >= booked_cutoff_dt)
                    .where(CallOutcome.outcome.in_(("booked", "not_interested")))
                )
            )
            .scalars()
            .all()
        )

        open_truck_posts = (
            (
                await s.execute(
                    select(CapacityPost)
                    .where(CapacityPost.status == "open")
                    .where(CapacityPost.kind == "truck")
                    .order_by(CapacityPost.created_at.desc())
                    .limit(_CAPACITY_MATCH_LIMIT)
                )
            )
            .scalars()
            .all()
        )

        # Only pull the 25k-scale ``all_leads`` set when there is at least one
        # open truck post to match it against. Without posts the nested loop
        # below is a no-op anyway — skipping the ORM hydrate here keeps the
        # 25k perf case well under the 150 ms /overview budget. Correctness
        # is unchanged: ``capacity_match_rows`` ends up empty either way.
        if open_truck_posts:
            all_leads = (
                await s.execute(select(Lead).where(Lead.state.isnot(None)))
            ).scalars().all()
        else:
            all_leads = []

    # Build the capacity_match previews — top broker per open post. Dedupe by
    # lead_id so the same broker never appears twice in Do next.
    capacity_match_rows: list[tuple[CapacityPost, Lead, int, str]] = []
    seen_lead_ids: set[str] = set()
    for post in open_truck_posts:
        best: tuple[int, str, Lead] | None = None
        for lead in all_leads:
            sug = score_broker_for_post(post, lead)
            key = (sug.score, lead.id)
            if best is None or key > (best[0], best[2].id):
                best = (sug.score, sug.reason, lead)
        if best and best[0] > 0 and best[2].id not in seen_lead_ids:
            capacity_match_rows.append((post, best[2], best[0], best[1]))
            seen_lead_ids.add(best[2].id)

    merged: list[tuple[int, int, str, Any]] = []
    for r in call_rows:
        merged.append(
            (
                _PRIORITY["call"],
                r.score,
                r.lead_id,
                DoNextCallRow(
                    kind="call",
                    lead_id=r.lead_id,
                    name=r.name,
                    state=r.state,
                    phone=r.phone,
                    score=r.score,
                    reason=r.opener or (r.reasons[0] if r.reasons else ""),
                ),
            )
        )
    for post, lead, score, reason in capacity_match_rows:
        merged.append(
            (
                _PRIORITY["capacity_match"],
                score,
                post.id,
                DoNextCapacityMatchRow(
                    kind="capacity_match",
                    post_id=post.id,
                    equipment=post.equipment,
                    origin_state=post.origin_state,
                    lead_id=lead.id,
                    lead_name=lead.name,
                    reason=f"{reason} — truck in {post.origin_state}",
                ),
            )
        )
    for lead in new_leads_rows:
        merged.append(
            (
                _PRIORITY["new_lead"],
                0,
                lead.id,
                DoNextNewLeadRow(
                    kind="new_lead",
                    lead_id=lead.id,
                    name=lead.name,
                    state=lead.state,
                    kind_label=lead.kind,
                    first_seen_at=(
                        lead.first_seen_at.isoformat() if lead.first_seen_at else ""
                    ),
                ),
            )
        )

    merged.sort(key=lambda t: (-t[0], -t[1], t[2]))
    do_next = [m[3] for m in merged[:_DO_NEXT_LIMIT]]

    series_counts: dict[str, dict[str, int]] = {}
    for o in outcomes_90d:
        if o.logged_at is None:
            continue
        key = o.logged_at.astimezone(ET).strftime("%Y-%m")
        bucket = series_counts.setdefault(key, {"booked": 0, "rejected": 0})
        if o.outcome == "booked":
            bucket["booked"] += 1
        elif o.outcome == "not_interested":
            bucket["rejected"] += 1

    demo = len(outcomes_90d) < _LOADS_BOOKED_DEMO_FLOOR
    series: list[MonthPointRow] = (
        []
        if demo
        else [
            MonthPointRow(month=m, booked=v["booked"], rejected=v["rejected"])
            for m, v in sorted(series_counts.items())
        ]
    )

    return OverviewTodayResult(
        date=today.isoformat(),
        tiles=TilesRow(
            to_call_today=TileCountRow(value=to_call_today_count, href="/call-list"),
            new_leads_since_last_crawl=NewLeadsTileRow(
                value=new_leads_count, since=new_leads_since, href="/leads"
            ),
            loads_booked_90d=LoadsBookedTileRow(
                value=booked_90d, demo=demo, href="/intelligence"
            ),
        ),
        do_next=do_next,
        booked_vs_rejected=BookedVsRejectedRow(demo=demo, series=series),
    )
