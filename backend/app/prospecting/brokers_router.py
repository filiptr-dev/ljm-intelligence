"""Brokers API — thin router over ``app.prospecting.brokers_service``.

Three GET routes, all owner-only (wired at ``main.py``):

  GET  /brokers                — ranked list with contact summary + next-action chip.
  GET  /brokers/{id}           — full contact block + computed next action + timeline.
  GET  /brokers/{id}/activity  — paged timeline tail for the detail page.

Business logic (next-action rule table, DB input assembly, deterministic sort)
lives in the service. Cursor encoding stays here — it is HTTP sugar.
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from app.prospecting.broker_rank_service import (
    RankFilters,
    rank_brokers as svc_rank_brokers,
)
from app.analysis.analysis_router import LeadNextStepOut
from app.prospecting.brokers_service import (
    ActivityCallEvent,
    ActivityEmailEvent,
    BrokerRowData,
    ContactField,
    EmailAnalyticsRow,
    NamedContactRow,
    NotFoundError,
    OverviewMetricsRow,
    fetch_ai_next_steps as svc_fetch_ai_next_steps,
    get_activity_page as svc_get_activity_page,
    get_detail as svc_get_detail,
    get_objections as svc_get_objections,
    list_brokers as svc_list_brokers,
)

router = APIRouter(prefix="/brokers", tags=["brokers"])


# ---------- response schemas -----------------------------------------------


class ContactFieldOut(BaseModel):
    value: str | None = None
    source: str | None = None
    verified_at: str | None = None
    confidence: str | None = None


class NextActionOut(BaseModel):
    kind: Literal["call", "email", "follow_up", "wait"]
    reason: str
    due_at: str | None = None


class NamedContactOut(BaseModel):
    id: int
    name: ContactFieldOut
    title: ContactFieldOut
    email: ContactFieldOut
    phone: ContactFieldOut
    is_decision_maker: bool
    pipeline_status: str
    sighted_count: int
    linkedin_url: str | None = None


class MonthlyPointOut(BaseModel):
    month: str
    booked: int
    rejected: int
    sent: int
    replied: int


class OverviewMetricsOut(BaseModel):
    health_score: int
    health_delta: int | None = None
    health_thin: bool
    health_components: dict[str, float]
    win_rate: float | None = None
    win_rate_thin: bool
    booked_12m: int
    rejected_12m: int
    sent_30d: int
    replied_30d: int
    reply_rate: float | None = None
    avg_reply_hours: float | None = None
    tone_30d: float | None = None
    has_bounce: bool
    has_suppression: bool
    monthly_series: list[MonthlyPointOut]
    last_contact_at: str | None = None
    days_since_last_contact: int | None = None
    segment: Literal[
        "all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral"
    ]
    revenue_usd: float = 0.0


class BrokerRowOut(BaseModel):
    id: str
    name: str
    mc: str | None = None
    dot: str | None = None
    state: str
    city: str | None = None
    phone: ContactFieldOut
    primary_email: ContactFieldOut
    fit_score: int | None = None
    next_action: NextActionOut
    last_activity_at: str | None = None
    # Opt-in via ?include=overview_metrics on GET /brokers. None otherwise so
    # the default list payload stays byte-identical to pre-overview callers.
    overview: OverviewMetricsOut | None = None
    # Cached AI "what next" per broker (see migration 0033 /
    # LeadAiSummary.next_step). Populated via a bulk per-page lookup — never
    # a live LLM call on list load. ``ai_summary_status`` lets the UI pick
    # between "show label" and "Generate" without a second request. Defaults
    # keep the wire shape backwards-compatible for pre-change callers.
    ai_next_step: LeadNextStepOut | None = None
    ai_summary_status: Literal["ready", "none"] = "none"


class SegmentsCountOut(BaseModel):
    all: int = 0
    hot: int = 0
    warm: int = 0
    payment_issues: int = 0
    dormant: int = 0
    not_interested: int = 0
    neutral: int = 0


class BrokerListOut(BaseModel):
    items: list[BrokerRowOut]
    next_cursor: str | None = None
    total: int
    segments_count: SegmentsCountOut | None = None


class ActivityCallOut(BaseModel):
    kind: Literal["call_outcome"] = "call_outcome"
    outcome: str
    logged_at: str
    note: str | None = None


class ActivityEmailOut(BaseModel):
    kind: Literal["email_sent"] = "email_sent"
    subject: str | None = None
    to_email: str
    sent_at: str
    replied_at: str | None = None
    mode: str


class ActivityPageOut(BaseModel):
    items: list[ActivityCallOut | ActivityEmailOut]
    next_cursor: str | None = None


class ObjectionItemOut(BaseModel):
    """One entry on the "Why they said no" card.

    ``source`` tells the UI which icon / tone to render ("call" | "suppression").
    No bounce source yet — ``sent_log`` has no bounce flag today; added when
    the Gmail connector lands bounce signals (plan: bounce source goes here
    without a shape change).
    """

    source: Literal["call", "suppression"]
    logged_at: str
    text: str
    contact_name: str | None = None


class ObjectionsOut(BaseModel):
    items: list[ObjectionItemOut]


class LastCallOut(BaseModel):
    outcome: str
    logged_at: str


class LastEmailOut(BaseModel):
    subject: str | None = None
    sent_at: str
    replied_at: str | None = None


class BrokerSummaryOut(BaseModel):
    sent_count_30d: int
    reply_count_30d: int
    last_call: LastCallOut | None = None
    last_email: LastEmailOut | None = None


class MainLaneOut(BaseModel):
    """Origin → destination summary rendered on the detail-page right rail.

    All fields optional. The card is hidden entirely when every field is
    null, so we never render dead air.
    """

    origin: str | None = None
    destination: str | None = None
    miles_band: str | None = None
    last_seen_at: str | None = None


class EmailAnalyticsBucketOut(BaseModel):
    """Winning dow or hour bucket. Only one of ``dow`` / ``hour`` is set."""

    dow: int | None = None
    hour: int | None = None
    reply_rate: float
    sample: int


class WeeklyPointOut(BaseModel):
    week_start: str
    sent: int
    replied: int


class EmailAnalyticsOut(BaseModel):
    """Honest email analytics for the broker detail page.

    Every scalar is nullable so the UI renders ``—`` for empty cells;
    ``weekly_series_12w`` is always exactly 12 entries, oldest first.
    """

    sent_30d: int
    sent_90d: int
    replied_30d: int
    replied_90d: int
    reply_rate_30d: float | None = None
    reply_rate_90d: float | None = None
    avg_reply_hours: float | None = None
    median_reply_hours: float | None = None
    best_day_of_week: EmailAnalyticsBucketOut | None = None
    best_hour_et: EmailAnalyticsBucketOut | None = None
    weekly_series_12w: list[WeeklyPointOut]


class BrokerDetailBody(BrokerRowOut):
    address: ContactFieldOut
    linkedin_company_url: str | None = None
    website_url: str | None = None
    contacts: list[NamedContactOut]
    main_lane: MainLaneOut | None = None


class BrokerDetailOut(BaseModel):
    broker: BrokerDetailBody
    activity: list[ActivityCallOut | ActivityEmailOut]
    summary: BrokerSummaryOut
    overview_metrics: OverviewMetricsOut | None = None
    email_analytics: EmailAnalyticsOut | None = None


# ---------- cursor helpers --------------------------------------------------


def _encode_cursor(ident: str) -> str:
    return base64.urlsafe_b64encode(ident.encode()).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        return base64.urlsafe_b64decode(padded.encode("ascii")).decode()
    except Exception as exc:
        raise HTTPException(status_code=400, detail="invalid cursor") from exc


# ---------- projection ------------------------------------------------------


def _field_out(f: ContactField) -> ContactFieldOut:
    return ContactFieldOut(**f.__dict__)


def _email_analytics_out(ea: EmailAnalyticsRow) -> EmailAnalyticsOut:
    return EmailAnalyticsOut(
        sent_30d=ea.sent_30d,
        sent_90d=ea.sent_90d,
        replied_30d=ea.replied_30d,
        replied_90d=ea.replied_90d,
        reply_rate_30d=ea.reply_rate_30d,
        reply_rate_90d=ea.reply_rate_90d,
        avg_reply_hours=ea.avg_reply_hours,
        median_reply_hours=ea.median_reply_hours,
        best_day_of_week=(
            EmailAnalyticsBucketOut(
                dow=ea.best_day_of_week.dow,
                reply_rate=ea.best_day_of_week.reply_rate,
                sample=ea.best_day_of_week.sample,
            )
            if ea.best_day_of_week is not None
            else None
        ),
        best_hour_et=(
            EmailAnalyticsBucketOut(
                hour=ea.best_hour_et.hour,
                reply_rate=ea.best_hour_et.reply_rate,
                sample=ea.best_hour_et.sample,
            )
            if ea.best_hour_et is not None
            else None
        ),
        weekly_series_12w=[
            WeeklyPointOut(week_start=w.week_start, sent=w.sent, replied=w.replied)
            for w in ea.weekly_series_12w
        ],
    )


def _overview_out(om: OverviewMetricsRow) -> OverviewMetricsOut:
    return OverviewMetricsOut(
        health_score=om.health_score,
        health_delta=om.health_delta,
        health_thin=om.health_thin,
        health_components=om.health_components,
        win_rate=om.win_rate,
        win_rate_thin=om.win_rate_thin,
        booked_12m=om.booked_12m,
        rejected_12m=om.rejected_12m,
        sent_30d=om.sent_30d,
        replied_30d=om.replied_30d,
        reply_rate=om.reply_rate,
        avg_reply_hours=om.avg_reply_hours,
        tone_30d=om.tone_30d,
        has_bounce=om.has_bounce,
        has_suppression=om.has_suppression,
        monthly_series=[
            MonthlyPointOut(month=p.month, booked=p.booked, rejected=p.rejected, sent=p.sent, replied=p.replied)
            for p in om.monthly_series
        ],
        last_contact_at=om.last_contact_at,
        days_since_last_contact=om.days_since_last_contact,
        segment=om.segment,  # type: ignore[arg-type]
        revenue_usd=om.revenue_usd,
    )


def _ai_next_step_out(raw: dict | None) -> LeadNextStepOut | None:
    """Project the stored ``next_step`` JSON into the wire DTO.

    Returns ``None`` for legacy rows (``next_step`` was NULL before migration
    0033) or any payload missing the required ``label`` so the UI falls
    through to the muted em-dash state.
    """
    if not isinstance(raw, dict):
        return None
    label = raw.get("label")
    if not isinstance(label, str) or not label:
        return None
    detail = raw.get("detail") or ""
    return LeadNextStepOut(label=label, detail=detail if isinstance(detail, str) else "")


def _row_out(
    r: BrokerRowData,
    overview: OverviewMetricsRow | None = None,
    *,
    ai_lookup: dict[str, dict | None] | None = None,
) -> BrokerRowOut:
    ai_status: Literal["ready", "none"] = "none"
    ai_next_step_out: LeadNextStepOut | None = None
    if ai_lookup is not None and r.id in ai_lookup:
        ai_status = "ready"
        ai_next_step_out = _ai_next_step_out(ai_lookup[r.id])
    return BrokerRowOut(
        id=r.id,
        name=r.name,
        mc=r.mc,
        dot=r.dot,
        state=r.state,
        city=r.city,
        phone=_field_out(r.phone),
        primary_email=_field_out(r.primary_email),
        fit_score=r.fit_score,
        next_action=NextActionOut(
            kind=r.next_action.kind,  # type: ignore[arg-type]
            reason=r.next_action.reason,
            due_at=r.next_action.due_at,
        ),
        last_activity_at=r.last_activity_at,
        overview=_overview_out(overview) if overview is not None else None,
        ai_next_step=ai_next_step_out,
        ai_summary_status=ai_status,
    )


def _event_out(ev: ActivityCallEvent | ActivityEmailEvent) -> ActivityCallOut | ActivityEmailOut:
    if isinstance(ev, ActivityCallEvent):
        return ActivityCallOut(outcome=ev.outcome, logged_at=ev.logged_at, note=ev.note)
    return ActivityEmailOut(
        subject=ev.subject,
        to_email=ev.to_email,
        sent_at=ev.sent_at,
        replied_at=ev.replied_at,
        mode=ev.mode,
    )


def _named_contact_out(c: NamedContactRow) -> NamedContactOut:
    return NamedContactOut(
        id=c.id,
        name=_field_out(c.name),
        title=_field_out(c.title),
        email=_field_out(c.email),
        phone=_field_out(c.phone),
        is_decision_maker=c.is_decision_maker,
        pipeline_status=c.pipeline_status,
        sighted_count=c.sighted_count,
        linkedin_url=c.linkedin_url,
    )


# ---------- GET /brokers ---------------------------------------------------


@router.get("", response_model=BrokerListOut)
async def list_brokers(
    request: Request,
    state: str | None = Query(default=None, min_length=2, max_length=2),
    min_fit: int | None = Query(default=None, ge=0, le=100),
    has_email: bool | None = Query(default=None),
    has_phone: bool | None = Query(default=None),
    next_action: Literal["call", "email", "follow_up", "wait"] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None),
    include: str | None = Query(default=None, description="Comma-sep. 'overview_metrics' adds the per-row overview block + segments_count."),
    segment: Literal["all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral"] | None = Query(default=None),
    sort: Literal["health", "win_rate", "booked", "rejected", "days_since", "name"] | None = Query(default=None),
) -> BrokerListOut:
    include_set = {s.strip() for s in (include or "").split(",") if s.strip()}
    include_overview = "overview_metrics" in include_set

    # Hot path: default list (no overview block, no segment, no overview-driven
    # sort) → SQL-ranked + keyset-paged via ``broker_rank_service``. The old
    # Python-ranked service (which eagerly loads every broker to compute the
    # overview block for segment/sort) stays wired for the ``include=
    # overview_metrics`` surface — that work is bounded by the broker count,
    # not by paging, and the perf-sensitive callers (brokers island, overview
    # tile, CSV export without overview) never hit it. See plan 2026-10-01.
    if not include_overview and segment is None and sort is None:
        page = await svc_rank_brokers(
            request.app.state.sessionmaker,
            filters=RankFilters(
                state=state,
                min_fit=min_fit,
                has_email=has_email,
                has_phone=has_phone,
                next_action=next_action,
                q=q,
            ),
            limit=limit,
            cursor=cursor,
        )
        ai_lookup = await svc_fetch_ai_next_steps(
            request.app.state.sessionmaker, [r.id for r in page.rows]
        )
        return BrokerListOut(
            items=[_row_out(r, ai_lookup=ai_lookup) for r in page.rows],
            next_cursor=page.next_cursor,
            total=page.total,
            segments_count=None,
        )

    result = await svc_list_brokers(
        request.app.state.sessionmaker,
        state=state,
        min_fit=min_fit,
        has_email=has_email,
        has_phone=has_phone,
        next_action=next_action,
        q=q,
        include_overview=include_overview,
        segment=segment if include_overview else None,
        sort=sort if include_overview else None,
    )

    # Cursor is the lead id to start AFTER.
    start = 0
    if cursor:
        anchor = _decode_cursor(cursor)
        for i, keys in enumerate(result.sort_keys):
            if keys[3] == anchor:
                start = i + 1
                break

    page = result.items[start : start + limit]
    next_cursor = None
    if start + limit < len(result.items) and page:
        last_id = result.sort_keys[start + limit - 1][3]
        next_cursor = _encode_cursor(last_id)

    seg_count_out: SegmentsCountOut | None = None
    if include_overview and result.overview_segments_count:
        seg_count_out = SegmentsCountOut(**result.overview_segments_count)

    ai_lookup = await svc_fetch_ai_next_steps(
        request.app.state.sessionmaker, [r.id for r in page]
    )
    return BrokerListOut(
        items=[
            _row_out(
                r,
                result.overview.get(r.id) if include_overview else None,
                ai_lookup=ai_lookup,
            )
            for r in page
        ],
        next_cursor=next_cursor,
        total=result.total,
        segments_count=seg_count_out,
    )


# ---------- GET /brokers/overview-summary ---------------------------------


class OverviewSummaryOut(BaseModel):
    """Companion payload for the brokers island.

    ``items`` is a map keyed by ``lead_id`` so the frontend can fire this in
    parallel with the fast ``GET /brokers`` list and splice overview columns
    (health, win-rate, sparkline, segment) onto the already-rendered rows.
    ``segments_count`` feeds the pill-row above the table.

    This endpoint exists because the slow ``include=overview_metrics`` path
    on ``GET /brokers`` fully computes per-broker overview blocks over the
    whole filtered set — too expensive to block LCP on. The split keeps
    the list route on the sub-sub-second SQL ranker without dropping any
    UI column.
    """

    items: dict[str, OverviewMetricsOut]
    segments_count: SegmentsCountOut


@router.get("/overview-summary", response_model=OverviewSummaryOut)
async def get_brokers_overview_summary(
    request: Request,
    state: str | None = Query(default=None, min_length=2, max_length=2),
    min_fit: int | None = Query(default=None, ge=0, le=100),
    has_email: bool | None = Query(default=None),
    has_phone: bool | None = Query(default=None),
    next_action: Literal["call", "email", "follow_up", "wait"] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    segment: Literal["all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral"] | None = Query(default=None),
    sort: Literal["health", "win_rate", "booked", "rejected", "days_since", "name"] | None = Query(default=None),
) -> OverviewSummaryOut:
    """Overview block + segment counts for the brokers island.

    Shares every filter with ``GET /brokers`` so the two calls return a
    consistent set. ``segment``/``sort`` are honoured when present — the
    segment pills' active state and overview-sorted columns both route
    through here.
    """
    result = await svc_list_brokers(
        request.app.state.sessionmaker,
        state=state,
        min_fit=min_fit,
        has_email=has_email,
        has_phone=has_phone,
        next_action=next_action,
        q=q,
        include_overview=True,
        segment=segment,
        sort=sort,
    )
    segments = SegmentsCountOut(**(result.overview_segments_count or {}))
    return OverviewSummaryOut(
        items={lid: _overview_out(om) for lid, om in result.overview.items()},
        segments_count=segments,
    )


# ---------- GET /brokers.csv ----------------------------------------------


@router.get(".csv")
async def export_brokers_csv(
    request: Request,
    state: str | None = Query(default=None, min_length=2, max_length=2),
    min_fit: int | None = Query(default=None, ge=0, le=100),
    has_email: bool | None = Query(default=None),
    has_phone: bool | None = Query(default=None),
    next_action: Literal["call", "email", "follow_up", "wait"] | None = Query(default=None),
    q: str | None = Query(default=None, max_length=128),
    segment: Literal["all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral"] | None = Query(default=None),
    sort: Literal["health", "win_rate", "booked", "rejected", "days_since", "name"] | None = Query(default=None),
):
    """CSV export of the current overview — flat columns matching the restored
    table. Writes the full result set (no cursor paging) so ops teams can pull
    one segment at a time without clicking "Load more"."""
    import csv as _csv
    import io as _io

    from fastapi.responses import StreamingResponse

    # CSV formula-injection guard: Excel / Sheets / Numbers all evaluate a
    # cell whose first character is one of these as a formula, so a broker
    # named "=SUM(1+1)" or a note starting with "@" or "-" could be exploited
    # by an operator opening the export. Prefix with a single quote so the
    # spreadsheet renders the text literally; we apply this to every string
    # field that could carry operator- or crawler-sourced text.
    _CSV_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")

    def _csv_safe(v: str | None) -> str:
        if v is None:
            return ""
        return f"'{v}" if v and v[0] in _CSV_FORMULA_PREFIXES else v

    result = await svc_list_brokers(
        request.app.state.sessionmaker,
        state=state,
        min_fit=min_fit,
        has_email=has_email,
        has_phone=has_phone,
        next_action=next_action,
        q=q,
        include_overview=True,
        segment=segment,
        sort=sort,
    )
    buf = _io.StringIO()
    w = _csv.writer(buf)
    w.writerow(
        [
            "id", "name", "mc", "dot", "state", "city",
            "health_score", "health_delta", "win_rate",
            "booked_12m", "rejected_12m",
            "sent_30d", "replied_30d", "reply_rate", "avg_reply_hours",
            "days_since_last_contact", "segment",
            "has_bounce", "has_suppression",
            "next_action_kind", "next_action_reason",
            "phone", "primary_email",
        ]
    )
    for r in result.items:
        om = result.overview.get(r.id)
        w.writerow(
            [
                r.id, _csv_safe(r.name), r.mc or "", r.dot or "", r.state, _csv_safe(r.city),
                om.health_score if om else "",
                om.health_delta if om and om.health_delta is not None else "",
                f"{om.win_rate:.4f}" if om and om.win_rate is not None else "",
                om.booked_12m if om else "",
                om.rejected_12m if om else "",
                om.sent_30d if om else "",
                om.replied_30d if om else "",
                f"{om.reply_rate:.4f}" if om and om.reply_rate is not None else "",
                f"{om.avg_reply_hours:.2f}" if om and om.avg_reply_hours is not None else "",
                om.days_since_last_contact if om and om.days_since_last_contact is not None else "",
                om.segment if om else "",
                "1" if om and om.has_bounce else "0",
                "1" if om and om.has_suppression else "0",
                r.next_action.kind, _csv_safe(r.next_action.reason),
                _csv_safe(r.phone.value), _csv_safe(r.primary_email.value),
            ]
        )
    buf.seek(0)
    filename = f"brokers-{segment or 'all'}.csv"
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------- GET /brokers/{id} ---------------------------------------------


@router.get("/{broker_id}", response_model=BrokerDetailOut)
async def get_broker(request: Request, broker_id: str) -> BrokerDetailOut:
    try:
        result = await svc_get_detail(request.app.state.sessionmaker, broker_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="broker not found") from exc

    base = _row_out(result.broker)
    main_lane_out: MainLaneOut | None = None
    if result.main_lane is not None:
        main_lane_out = MainLaneOut(
            origin=result.main_lane.origin,
            destination=result.main_lane.destination,
            miles_band=result.main_lane.miles_band,
            last_seen_at=result.main_lane.last_seen_at,
        )
    detail = BrokerDetailBody(
        **base.model_dump(),
        address=_field_out(result.address),
        linkedin_company_url=result.linkedin_company_url,
        website_url=result.website_url,
        contacts=[_named_contact_out(c) for c in result.contacts],
        main_lane=main_lane_out,
    )
    summary = BrokerSummaryOut(
        sent_count_30d=result.summary.sent_count_30d,
        reply_count_30d=result.summary.reply_count_30d,
        last_call=LastCallOut(**result.summary.last_call.__dict__) if result.summary.last_call else None,
        last_email=LastEmailOut(**result.summary.last_email.__dict__) if result.summary.last_email else None,
    )
    return BrokerDetailOut(
        broker=detail,
        activity=[_event_out(e) for e in result.activity],
        summary=summary,
        overview_metrics=(
            _overview_out(result.overview_metrics)
            if result.overview_metrics is not None
            else None
        ),
        email_analytics=(
            _email_analytics_out(result.email_analytics)
            if result.email_analytics is not None
            else None
        ),
    )


# ---------- GET /brokers/{id}/activity ------------------------------------


@router.get("/{broker_id}/activity", response_model=ActivityPageOut)
async def get_broker_activity(
    request: Request,
    broker_id: str,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = Query(default=None),
) -> ActivityPageOut:
    before: datetime | None = None
    if cursor:
        raw = _decode_cursor(cursor)
        try:
            before = datetime.fromisoformat(raw) if raw else None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="invalid cursor") from exc

    try:
        events, has_more = await svc_get_activity_page(
            request.app.state.sessionmaker, broker_id, limit=limit, before=before
        )
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="broker not found") from exc

    next_cursor: str | None = None
    if has_more and events:
        last = events[-1]
        last_at = last.logged_at if isinstance(last, ActivityCallEvent) else last.sent_at
        next_cursor = _encode_cursor(last_at)

    return ActivityPageOut(items=[_event_out(e) for e in events], next_cursor=next_cursor)


# ---------- GET /brokers/{id}/objections ----------------------------------


@router.get("/{broker_id}/objections", response_model=ObjectionsOut)
async def get_broker_objections(request: Request, broker_id: str) -> ObjectionsOut:
    """Why they said no — reasons rolled up for the detail card.

    Reads from ``call_outcomes`` where ``outcome='not_interested'`` with the
    operator-authored note, plus ``suppression`` rows keyed to any of the
    broker's known email addresses (opt-outs, do-not-contact). Newest first,
    capped at 25.
    """
    try:
        items = await svc_get_objections(request.app.state.sessionmaker, broker_id)
    except NotFoundError as exc:
        raise HTTPException(status_code=404, detail="broker not found") from exc
    return ObjectionsOut(
        items=[
            ObjectionItemOut(
                source=i.source,  # type: ignore[arg-type]
                logged_at=i.logged_at,
                text=i.text,
                contact_name=i.contact_name,
            )
            for i in items
        ]
    )
