"""Overview — the operator's Today desk.

One route, one aggregate read:

  GET /overview/today  → OverviewTodayOut

Composition (deliberately shallow — the real work lives in sibling tasks):

  1. tiles.to_call_today — ``len(rank_call_list(...))`` on the same inputs
     ``call_list.py`` uses. Reuses the ranker so the Today tile and the Call
     List page can never disagree.
  2. tiles.new_leads_since_last_crawl — leads with ``first_seen_at >=
     max(CrawlRun.finished_at where status='done')``. If no completed crawl
     yet, falls back to leads created today ET (``since: null``) with the
     sub-text "no crawl yet — click Crawl now."
  3. tiles.loads_booked_90d — count of ``CallOutcome.outcome == 'booked'`` in
     the last 90 days; ``demo: true`` until there are ≥10 real outcomes.
  4. do_next[] — top-10 merged list across three sources (call, capacity
     match, new lead) sorted deterministically by (-priority, -score, id).
  5. booked_vs_rejected — per-month counts of ``booked`` vs
     ``not_interested`` over the last 90 days; ``demo: true`` with empty
     series when the total is <10 outcomes.

"Today" is computed in ``America/New_York`` — see the plan rationale. The
call-list route still uses UTC and is left for its own sibling task so we
don't tangle the two fixes.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.call_list import _load_and_rank
from app.models import CallOutcome, CapacityPost, CrawlRun, Lead
from app.pipeline.match import score_broker_for_post

router = APIRouter(prefix="/overview", tags=["overview"])

ET = ZoneInfo("America/New_York")

# Deterministic merge weights for ``do_next[]`` — kept here so changing them
# is a one-line review.
_PRIORITY = {"call": 3, "capacity_match": 2, "new_lead": 1}
_DO_NEXT_LIMIT = 10
_CALL_TOP_N = 5
_NEW_LEADS_PREVIEW = 3
_CAPACITY_MATCH_LIMIT = 3
_LOADS_BOOKED_DEMO_FLOOR = 10


# ---------- schemas ---------------------------------------------------------


class TileCount(BaseModel):
    value: int
    href: str


class NewLeadsTile(BaseModel):
    value: int
    since: str | None
    href: str


class LoadsBookedTile(BaseModel):
    value: int
    demo: bool
    href: str


class Tiles(BaseModel):
    to_call_today: TileCount
    new_leads_since_last_crawl: NewLeadsTile
    loads_booked_90d: LoadsBookedTile


class DoNextCall(BaseModel):
    # ``Literal`` on the discriminator so the generated TS schema narrows
    # the ``do_next`` union — otherwise ``kind: str`` erases the type and
    # the frontend can't tell a call row from a capacity match.
    kind: Literal["call"] = "call"
    lead_id: str
    name: str
    state: str
    phone: str
    score: int
    reason: str
    href: str = "/call-list"


class DoNextCapacityMatch(BaseModel):
    kind: Literal["capacity_match"] = "capacity_match"
    post_id: str
    equipment: str
    origin_state: str
    lead_id: str
    lead_name: str
    reason: str
    href: str = "/capacity"


class DoNextNewLead(BaseModel):
    kind: Literal["new_lead"] = "new_lead"
    lead_id: str
    name: str
    state: str
    kind_label: str
    first_seen_at: str
    href: str = "/leads"


class MonthPoint(BaseModel):
    month: str
    booked: int
    rejected: int


class BookedVsRejected(BaseModel):
    demo: bool
    series: list[MonthPoint]


class HeaderActions(BaseModel):
    crawl_now_href: str = "/leads"
    new_post_href: str = "/capacity?new=1"


class OverviewTodayOut(BaseModel):
    date: str
    tiles: Tiles
    do_next: list[DoNextCall | DoNextCapacityMatch | DoNextNewLead]
    booked_vs_rejected: BookedVsRejected
    header: HeaderActions


# ---------- helpers ---------------------------------------------------------


def _today_et() -> date:
    """Today in America/New_York.

    Isolated so a test can patch it. UTC midnight rolling the Today list at
    8pm ET is the kind of bug that erodes trust before anyone can file a
    ticket; the ET boundary avoids it.
    """
    return datetime.now(tz=ET).date()


def _et_start_of_day(d: date) -> datetime:
    """ET midnight of ``d`` as an aware ``datetime`` (for SQL comparisons)."""
    return datetime(d.year, d.month, d.day, tzinfo=ET)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


# ---------- route -----------------------------------------------------------


@router.get("/today", response_model=OverviewTodayOut)
async def get_today(request: Request) -> OverviewTodayOut:
    sessionmaker = request.app.state.sessionmaker
    today = _today_et()
    et_midnight = _et_start_of_day(today)

    # Reuse the call-list adapter so the Today tile and the Call List page
    # read from the same ranker on the same inputs — a second ordering here
    # would drift the moment the ranker changes. ``to_call_today`` is the full
    # ranked length; ``do_next`` only surfaces the top five.
    full_call_rows = await _load_and_rank(sessionmaker, today, 100)
    call_rows = full_call_rows[:_CALL_TOP_N]

    async with sessionmaker() as s:
        # Latest completed crawl.
        last_crawl_at = (
            await s.execute(select(func.max(CrawlRun.finished_at)).where(CrawlRun.status == "done"))
        ).scalar_one_or_none()

        # New-leads tile. Normalise the threshold to UTC — SQLite stores
        # DateTime(timezone=True) columns as ISO strings and lexicographic
        # comparison on a mixed-offset threshold (e.g. ET midnight at
        # "-04:00") versus a stored "+00:00" row would be wrong at the
        # character level. Comparing in UTC closes that trap for both the
        # SQLite test driver and Postgres (which handles tz natively).
        if last_crawl_at is not None:
            new_leads_threshold = last_crawl_at
            if new_leads_threshold.tzinfo is not None:
                new_leads_threshold = new_leads_threshold.astimezone(UTC)
            new_leads_since = _iso(last_crawl_at)
        else:
            new_leads_threshold = et_midnight.astimezone(UTC)
            new_leads_since = None  # UI reads this as "no crawl yet"

        new_leads_count = (
            await s.execute(select(func.count(Lead.id)).where(Lead.first_seen_at >= new_leads_threshold))
        ).scalar_one()

        # Preview of 3 newest leads for the Do-next list.
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

        # Loads booked, 90d (CallOutcome.outcome == 'booked'). UTC-normalised
        # for the same reason as above.
        booked_cutoff_dt = (datetime.now(tz=ET) - timedelta(days=90)).astimezone(UTC)
        booked_90d = (
            await s.execute(
                select(func.count(CallOutcome.id))
                .where(CallOutcome.outcome == "booked")
                .where(CallOutcome.logged_at >= booked_cutoff_dt)
            )
        ).scalar_one()

        # Outcomes in last 90d (for the chart AND the demo flag).
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

        # Open truck posts for capacity_match preview.
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

        # All leads with phone/email — cheap enough and matches the match
        # scorer's expectations. The capacity page uses the same primitive.
        all_leads = (await s.execute(select(Lead).where(Lead.state.isnot(None)))).scalars().all()

    # Build the capacity_match previews — top broker per open post.
    # Dedupe by lead_id: if the same broker is the top match for two posts, keep
    # only the highest-scored pairing so the same name never appears twice in
    # Do next.
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

    # ---- Do-next merge ---------------------------------------------------

    merged: list[tuple[int, int, str, BaseModel]] = []

    for r in call_rows:
        merged.append(
            (
                _PRIORITY["call"],
                r.score,
                r.lead_id,
                DoNextCall(
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
                DoNextCapacityMatch(
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
                DoNextNewLead(
                    lead_id=lead.id,
                    name=lead.name,
                    state=lead.state,
                    kind_label=lead.kind,
                    first_seen_at=(lead.first_seen_at.isoformat() if lead.first_seen_at else ""),
                ),
            )
        )

    # Sort: (-priority, -score, id) — stable, deterministic.
    merged.sort(key=lambda t: (-t[0], -t[1], t[2]))
    do_next = [m[3] for m in merged[:_DO_NEXT_LIMIT]]

    # ---- Booked vs rejected (chart) --------------------------------------

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
    series: list[MonthPoint] = (
        []
        if demo
        else [MonthPoint(month=m, booked=v["booked"], rejected=v["rejected"]) for m, v in sorted(series_counts.items())]
    )

    return OverviewTodayOut(
        date=today.isoformat(),
        tiles=Tiles(
            to_call_today=TileCount(value=len(full_call_rows), href="/call-list"),
            new_leads_since_last_crawl=NewLeadsTile(value=new_leads_count, since=new_leads_since, href="/leads"),
            loads_booked_90d=LoadsBookedTile(value=booked_90d, demo=demo, href="/intelligence"),
        ),
        do_next=do_next,
        booked_vs_rejected=BookedVsRejected(demo=demo, series=series),
        header=HeaderActions(),
    )
