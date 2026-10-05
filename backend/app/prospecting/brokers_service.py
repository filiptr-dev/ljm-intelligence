"""Brokers service — real-data list + detail + activity timeline.

Thin business layer behind ``app/api/brokers.py``. Owns the DB reads, the
per-lead "next action" input assembly, and the paged activity merge. The
pure rule-table lives in ``app.pipeline.broker_next_action``.

Returns plain dataclasses. Cursor encoding stays in the router.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    CallOutcome,
    Lead,
    LeadContact,
    LeadContactProvenance,
    MailMessage,
    MessageInsight,
    SentLog,
    Suppression,
)
from app.pipeline.broker_next_action import (
    PRIORITY,
    NextAction,
    NextActionInput,
    compute,
)
from app.prospecting.broker_health import (
    HealthInputs,
    HealthScore,
    TONE_WINDOW_DAYS,
    VOLUME_WINDOW_DAYS,
    WIN_RATE_WINDOW_DAYS,
    compute_health,
)


class NotFoundError(Exception):
    """Raised when a broker id does not exist (router → 404)."""


# ---------- row shapes ------------------------------------------------------


@dataclass
class ContactField:
    value: str | None = None
    source: str | None = None
    verified_at: str | None = None
    confidence: str | None = None


@dataclass
class NextActionRow:
    kind: str  # "call" | "email" | "follow_up" | "wait"
    reason: str
    due_at: str | None


@dataclass
class NamedContactRow:
    id: int
    name: ContactField
    title: ContactField
    email: ContactField
    phone: ContactField
    is_decision_maker: bool
    pipeline_status: str
    sighted_count: int
    linkedin_url: str | None = None


@dataclass
class BrokerRowData:
    id: str
    name: str
    mc: str | None
    dot: str | None
    state: str
    city: str | None
    phone: ContactField
    primary_email: ContactField
    fit_score: int | None
    next_action: NextActionRow
    last_activity_at: str | None


@dataclass
class ActivityCallEvent:
    kind: str  # literal "call_outcome"
    outcome: str
    logged_at: str
    note: str | None = None


@dataclass
class ActivityEmailEvent:
    kind: str  # literal "email_sent"
    subject: str | None
    to_email: str
    sent_at: str
    replied_at: str | None
    mode: str


@dataclass
class BrokerListResult:
    items: list[BrokerRowData]
    total: int
    # Enriched tuples sorted deterministically so the router can slice by
    # cursor without re-sorting.
    sort_keys: list[tuple[int, int, str, str]] = field(default_factory=list)
    # Opt-in overview block, keyed by lead id. Empty when the caller does not
    # pass ``include_overview=True`` — keeps the default list payload
    # byte-identical to the pre-overview response.
    overview: dict[str, "OverviewMetricsRow"] = field(default_factory=dict)
    overview_segments_count: dict[str, int] = field(default_factory=dict)


@dataclass
class LastCallRow:
    outcome: str
    logged_at: str


@dataclass
class LastEmailRow:
    subject: str | None
    sent_at: str
    replied_at: str | None


@dataclass
class SummaryRow:
    sent_count_30d: int
    reply_count_30d: int
    last_call: LastCallRow | None
    last_email: LastEmailRow | None


@dataclass
class MainLaneRow:
    origin: str | None
    destination: str | None
    miles_band: str | None
    last_seen_at: str | None


@dataclass
class BrokerDetailResult:
    broker: BrokerRowData
    address: ContactField
    linkedin_company_url: str | None
    website_url: str | None
    contacts: list[NamedContactRow]
    activity: list[ActivityCallEvent | ActivityEmailEvent]
    summary: SummaryRow
    main_lane: MainLaneRow | None
    overview_metrics: "OverviewMetricsRow | None" = None


# ---------- helpers ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite tosses tz info; treat naive as UTC so arithmetic works everywhere.

    Mirrors ``app/inbox/service.py::_as_utc`` — same shape, kept local to avoid
    a cross-context import between prospecting and inbox.
    """
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _field(
    value: str | None,
    source: str | None = None,
    verified_at: datetime | None = None,
    confidence: str | None = None,
) -> ContactField:
    return ContactField(
        value=value or None,
        source=source,
        verified_at=_iso(verified_at),
        confidence=confidence,
    )


@dataclass
class _ContactAgg:
    email_count: int = 0
    phone_count: int = 0
    sendable_email_count: int = 0
    has_sendable_email: bool = False


async def _load_next_action_inputs(
    session: AsyncSession, lead_ids: list[str], today: date
) -> tuple[
    dict[str, CallOutcome | None],
    dict[str, SentLog | None],
    dict[str, CallOutcome],
    dict[str, _ContactAgg],
    dict[str, int],
    dict[str, int],
]:
    if not lead_ids:
        return {}, {}, {}, {}, {}, {}

    call_rows = (
        await session.execute(select(CallOutcome).where(CallOutcome.lead_id.in_(lead_ids)))
    ).scalars().all()
    latest_call: dict[str, CallOutcome] = {}
    pending_cb: dict[str, CallOutcome] = {}
    totals_call: dict[str, int] = {}
    for row in call_rows:
        totals_call[row.lead_id] = totals_call.get(row.lead_id, 0) + 1
        cur = latest_call.get(row.lead_id)
        if cur is None or row.logged_at > cur.logged_at:
            latest_call[row.lead_id] = row
        if row.outcome == "callback" and row.callback_at and row.callback_at <= today:
            existing = pending_cb.get(row.lead_id)
            if existing is None or row.logged_at < existing.logged_at:
                pending_cb[row.lead_id] = row

    sent_rows = (
        await session.execute(select(SentLog).where(SentLog.lead_id.in_(lead_ids)))
    ).scalars().all()
    latest_sent: dict[str, SentLog] = {}
    totals_sent: dict[str, int] = {}
    for row in sent_rows:
        totals_sent[row.lead_id] = totals_sent.get(row.lead_id, 0) + 1
        cur = latest_sent.get(row.lead_id)
        if cur is None or row.sent_at > cur.sent_at:
            latest_sent[row.lead_id] = row

    suppressed_emails = set(
        (await session.execute(select(Suppression.email))).scalars().all()
    )

    contact_rows = (
        await session.execute(select(LeadContact).where(LeadContact.lead_id.in_(lead_ids)))
    ).scalars().all()
    agg: dict[str, _ContactAgg] = {lid: _ContactAgg() for lid in lead_ids}
    for c in contact_rows:
        a = agg.setdefault(c.lead_id, _ContactAgg())
        if c.email:
            a.email_count += 1
            if c.pipeline_status != "bounced" and c.email not in suppressed_emails:
                a.sendable_email_count += 1
                a.has_sendable_email = True
        if c.phone:
            a.phone_count += 1

    return latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent


def _compute_action_for(
    lead: Lead,
    latest_call: CallOutcome | None,
    latest_sent: SentLog | None,
    pending_cb: CallOutcome | None,
    agg: _ContactAgg,
    totals_call: int,
    totals_sent: int,
    now: datetime,
    today: date,
) -> NextAction:
    pending_days: int | None = None
    if pending_cb is not None:
        delta = today - (pending_cb.logged_at.date() if pending_cb.logged_at else today)
        pending_days = max(0, delta.days)
    inp = NextActionInput(
        latest_call_outcome=latest_call.outcome if latest_call else None,
        latest_call_logged_at=latest_call.logged_at if latest_call else None,
        pending_callback_today=pending_cb is not None,
        pending_callback_days_ago=pending_days,
        pending_callback_due=pending_cb.callback_at if pending_cb else None,
        latest_sent_at=latest_sent.sent_at if latest_sent else None,
        latest_sent_replied_at=latest_sent.replied_at if latest_sent else None,
        has_phone=bool(lead.phone),
        has_sendable_email=agg.has_sendable_email,
        total_call_outcomes=totals_call,
        total_sent=totals_sent,
    )
    return compute(inp, now)


def _row_data(lead: Lead, action: NextAction, last_activity_at: datetime | None) -> BrokerRowData:
    return BrokerRowData(
        id=lead.id,
        name=lead.name,
        mc=lead.mc,
        dot=lead.dot,
        state=lead.state,
        city=lead.city,
        phone=_field(lead.phone, lead.phone_source),
        primary_email=_field(lead.primary_email, lead.primary_email_source),
        fit_score=lead.fit_score,
        next_action=NextActionRow(
            kind=action.kind,
            reason=action.reason,
            due_at=action.due_at.isoformat() if action.due_at else None,
        ),
        last_activity_at=_iso(last_activity_at),
    )


async def _load_activity(
    session: AsyncSession, lead_id: str, limit: int, before: datetime | None
) -> list[ActivityCallEvent | ActivityEmailEvent]:
    call_q = select(CallOutcome).where(CallOutcome.lead_id == lead_id)
    sent_q = select(SentLog).where(SentLog.lead_id == lead_id)
    if before is not None:
        call_q = call_q.where(CallOutcome.logged_at < before)
        sent_q = sent_q.where(SentLog.sent_at < before)
    calls = (
        await session.execute(call_q.order_by(desc(CallOutcome.logged_at)).limit(limit))
    ).scalars().all()
    sents = (
        await session.execute(sent_q.order_by(desc(SentLog.sent_at)).limit(limit))
    ).scalars().all()

    events: list[tuple[datetime, ActivityCallEvent | ActivityEmailEvent]] = []
    for c in calls:
        events.append(
            (
                c.logged_at,
                ActivityCallEvent(
                    kind="call_outcome",
                    outcome=c.outcome,
                    logged_at=c.logged_at.isoformat(),
                    note=c.note,
                ),
            )
        )
    for s_ in sents:
        events.append(
            (
                s_.sent_at,
                ActivityEmailEvent(
                    kind="email_sent",
                    subject=s_.subject,
                    to_email=s_.to_email,
                    sent_at=s_.sent_at.isoformat(),
                    replied_at=_iso(s_.replied_at),
                    mode=s_.mode,
                ),
            )
        )
    events.sort(key=lambda t: t[0], reverse=True)
    return [e[1] for e in events[:limit]]


# ---------- list -----------------------------------------------------------


async def list_brokers(
    sessionmaker: Any,
    *,
    state: str | None = None,
    min_fit: int | None = None,
    has_email: bool | None = None,
    has_phone: bool | None = None,
    next_action: str | None = None,
    q: str | None = None,
    include_overview: bool = False,
    segment: str | None = None,
    sort: str | None = None,
) -> BrokerListResult:
    """Fetch all matching brokers, compute next-action per row, return
    deterministically sorted ``(priority, -fit_score, name, row, lead_id)``
    tuples. Cursor paging/slicing happens in the router."""
    today = _now().date()

    conds = [Lead.kind == "Broker"]
    if state:
        conds.append(Lead.state == state.upper())
    if min_fit is not None:
        conds.append(Lead.fit_score >= min_fit)
    if has_email is True:
        conds.append(Lead.primary_email.isnot(None))
    elif has_email is False:
        conds.append(Lead.primary_email.is_(None))
    if has_phone is True:
        conds.append(Lead.phone.isnot(None))
    elif has_phone is False:
        conds.append(Lead.phone.is_(None))
    if q:
        pat = f"%{q.strip()}%"
        conds.append(or_(Lead.name.ilike(pat), Lead.mc.ilike(pat), Lead.dot.ilike(pat)))

    async with sessionmaker() as s:
        leads = (await s.execute(select(Lead).where(and_(*conds)))).scalars().all()
        lead_ids = [l.id for l in leads]
        latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent = (
            await _load_next_action_inputs(s, lead_ids, today)
        )

    now = _now()
    enriched: list[tuple[int, int, str, BrokerRowData, str]] = []
    for lead in leads:
        a = agg.get(lead.id, _ContactAgg())
        lc = latest_call.get(lead.id)
        ls = latest_sent.get(lead.id)
        pcb = pending_cb.get(lead.id)
        action = _compute_action_for(
            lead, lc, ls, pcb, a,
            totals_call.get(lead.id, 0),
            totals_sent.get(lead.id, 0),
            now, today,
        )
        if next_action and action.kind != next_action:
            continue
        last_activity = None
        for cand in (lc.logged_at if lc else None, ls.sent_at if ls else None):
            if cand is None:
                continue
            if last_activity is None or cand > last_activity:
                last_activity = cand
        row = _row_data(lead, action, last_activity)
        enriched.append(
            (
                PRIORITY[action.kind],
                -(lead.fit_score if lead.fit_score is not None else -1),
                (lead.name or "").lower(),
                row,
                lead.id,
            )
        )
    enriched.sort(key=lambda t: (t[0], t[1], t[2], t[4]))
    items = [t[3] for t in enriched]
    keys = [(t[0], t[1], t[2], t[4]) for t in enriched]

    overview: dict[str, OverviewMetricsRow] = {}
    seg_counts: dict[str, int] = {}
    if include_overview:
        action_by_lead: dict[str, NextAction] = {}
        latest_call_by_lead: dict[str, CallOutcome | None] = {}
        last_activity_by_lead: dict[str, datetime | None] = {}
        # Rebuild per-row state from the enriched tuples. The _compute_action_for
        # inputs are deterministic from (lead, latest_call, latest_sent, …) so
        # we can look up by lead_id here.
        for lead in leads:
            lc = latest_call.get(lead.id)
            ls = latest_sent.get(lead.id)
            pcb = pending_cb.get(lead.id)
            a = agg.get(lead.id, _ContactAgg())
            action = _compute_action_for(
                lead, lc, ls, pcb, a,
                totals_call.get(lead.id, 0),
                totals_sent.get(lead.id, 0),
                now, today,
            )
            action_by_lead[lead.id] = action
            latest_call_by_lead[lead.id] = lc
            la: datetime | None = None
            for cand in (lc.logged_at if lc else None, ls.sent_at if ls else None):
                if cand is None:
                    continue
                if la is None or cand > la:
                    la = cand
            last_activity_by_lead[lead.id] = la

        overview = await get_overview_metrics(
            sessionmaker,
            [l.id for l in leads],
            action_by_lead=action_by_lead,
            latest_call_by_lead=latest_call_by_lead,
            last_activity_by_lead=last_activity_by_lead,
        )
        seg_counts = segments_count(overview)
        # Segment filter (post-compute; segment is derived from overview).
        if segment and segment != "all":
            keep: list[tuple[BrokerRowData, tuple[int, int, str, str]]] = []
            for row, key in zip(items, keys):
                om = overview.get(row.id)
                if om is not None and om.segment == segment:
                    keep.append((row, key))
            items = [k[0] for k in keep]
            keys = [k[1] for k in keep]
        # Overview-driven sort override (health / win_rate / booked / rejected
        # / days_since / name). The default remains the next-action priority
        # ordering above when no sort is requested.
        if sort and sort in SORT_KEYS:
            items = resort(items, overview, sort)
            # Rebuild sort_keys to match the new order (cursor paging stays
            # stable because sort_keys[idx][3] = lead_id).
            key_by_id = {k[3]: k for k in keys}
            keys = [key_by_id[r.id] for r in items]

    return BrokerListResult(
        items=items,
        total=len(items),
        sort_keys=keys,
        overview=overview,
        overview_segments_count=seg_counts,
    )


# ---------- detail ---------------------------------------------------------


_MAX_CONTACTS = 100
_RECENT_ACTIVITY = 25


async def get_detail(sessionmaker: Any, broker_id: str) -> BrokerDetailResult:
    today = _now().date()
    now = _now()

    async with sessionmaker() as s:
        lead = (
            await s.execute(select(Lead).where(Lead.id == broker_id, Lead.kind == "Broker"))
        ).scalar_one_or_none()
        if lead is None:
            raise NotFoundError("broker not found")

        latest_call, latest_sent, pending_cb, agg, totals_call, totals_sent = (
            await _load_next_action_inputs(s, [lead.id], today)
        )

        contact_rows = (
            await s.execute(select(LeadContact).where(LeadContact.lead_id == lead.id))
        ).scalars().all()
        prov_counts_rows = (
            await s.execute(
                select(LeadContactProvenance.contact_id, func.count())
                .where(
                    LeadContactProvenance.contact_id.in_(
                        [c.id for c in contact_rows] or [-1]
                    )
                )
                .group_by(LeadContactProvenance.contact_id)
            )
        ).all()
        prov_counts: dict[int, int] = {cid: n for cid, n in prov_counts_rows}

        def _conf_rank(conf: str | None) -> int:
            return {"high": 0, "medium": 1, "low": 2}.get(conf or "", 3)

        contact_rows_sorted = sorted(
            contact_rows,
            key=lambda c: (
                0 if c.is_decision_maker else 1,
                _conf_rank(c.confidence),
                -prov_counts.get(c.id, 0),
            ),
        )[:_MAX_CONTACTS]

        activity = await _load_activity(s, lead.id, _RECENT_ACTIVITY, before=None)

        cutoff = now - timedelta(days=30)
        sent_30d = (
            await s.execute(
                select(func.count())
                .select_from(SentLog)
                .where(SentLog.lead_id == lead.id, SentLog.sent_at >= cutoff)
            )
        ).scalar_one()
        reply_30d = (
            await s.execute(
                select(func.count())
                .select_from(SentLog)
                .where(
                    SentLog.lead_id == lead.id,
                    SentLog.replied_at.isnot(None),
                    SentLog.replied_at >= cutoff,
                )
            )
        ).scalar_one()

    lc = latest_call.get(lead.id)
    ls = latest_sent.get(lead.id)
    pcb = pending_cb.get(lead.id)
    a = agg.get(lead.id, _ContactAgg())
    action = _compute_action_for(
        lead, lc, ls, pcb, a,
        totals_call.get(lead.id, 0),
        totals_sent.get(lead.id, 0),
        now, today,
    )
    last_activity = None
    for cand in (lc.logged_at if lc else None, ls.sent_at if ls else None):
        if cand is None:
            continue
        if last_activity is None or cand > last_activity:
            last_activity = cand

    broker_row = _row_data(lead, action, last_activity)

    contacts = [
        NamedContactRow(
            id=c.id,
            name=_field(c.name, c.source, c.last_verified_at, c.confidence),
            title=_field(c.title, c.source, c.last_verified_at, c.confidence),
            email=_field(c.email, c.source, c.last_verified_at, c.confidence),
            phone=_field(c.phone, c.source, c.last_verified_at, c.confidence),
            is_decision_maker=c.is_decision_maker,
            pipeline_status=c.pipeline_status,
            sighted_count=prov_counts.get(c.id, 0),
            linkedin_url=c.linkedin_url,
        )
        for c in contact_rows_sorted
    ]

    summary = SummaryRow(
        sent_count_30d=int(sent_30d or 0),
        reply_count_30d=int(reply_30d or 0),
        last_call=LastCallRow(outcome=lc.outcome, logged_at=lc.logged_at.isoformat()) if lc else None,
        last_email=(
            LastEmailRow(
                subject=ls.subject, sent_at=ls.sent_at.isoformat(), replied_at=_iso(ls.replied_at)
            )
            if ls
            else None
        ),
    )

    main_lane: MainLaneRow | None = None
    if any(
        getattr(lead, name) is not None
        for name in (
            "lane_origin_region",
            "lane_destination_region",
            "lane_miles_band",
            "lane_last_seen_at",
        )
    ):
        main_lane = MainLaneRow(
            origin=lead.lane_origin_region,
            destination=lead.lane_destination_region,
            miles_band=lead.lane_miles_band,
            last_seen_at=_iso(lead.lane_last_seen_at),
        )

    overview_map = await get_overview_metrics(
        sessionmaker,
        [lead.id],
        action_by_lead={lead.id: action},
        latest_call_by_lead={lead.id: lc},
        last_activity_by_lead={lead.id: last_activity},
    )

    return BrokerDetailResult(
        broker=broker_row,
        address=_field(lead.address, lead.address_source),
        linkedin_company_url=lead.linkedin_company_url,
        website_url=lead.website_url,
        contacts=contacts,
        activity=activity,
        summary=summary,
        main_lane=main_lane,
        overview_metrics=overview_map.get(lead.id),
    )


# ---------- activity (paged) -----------------------------------------------


@dataclass
class ObjectionItem:
    """One objection on the "Why they said no" card.

    * ``call`` — a ``call_outcomes`` row with ``outcome='not_interested'``
      and the operator-authored ``note`` (if any). ``text`` falls back to
      the literal ``"Not interested"`` when no note was logged.
    * ``suppression`` — a ``suppression`` row matching one of this broker's
      known email addresses. The ``text`` is the reason (``do_not_contact``
      or similar) with the email appended for operator context.
    """

    source: str
    logged_at: str
    text: str
    contact_name: str | None = None


_MAX_OBJECTIONS = 25


async def get_objections(sessionmaker: Any, broker_id: str) -> list[ObjectionItem]:
    """Return up to 25 objection items for ``broker_id``, newest first.

    Reads from two sources, no new tables:
      * ``call_outcomes.outcome='not_interested'`` on this lead.
      * ``suppression`` on any of this lead's contact emails.

    The sent_log bounce source listed in the plan is deferred — ``sent_log``
    has no bounce flag today; when the Gmail connector lands bounce
    signals, add a third branch that selects ``source='bounce'`` without
    shape changes.
    """
    async with sessionmaker() as s:
        lead = (
            await s.execute(select(Lead).where(Lead.id == broker_id, Lead.kind == "Broker"))
        ).scalar_one_or_none()
        if lead is None:
            raise NotFoundError("broker not found")

        contact_rows = (
            await s.execute(
                select(LeadContact.email, LeadContact.name)
                .where(LeadContact.lead_id == lead.id, LeadContact.email.isnot(None))
            )
        ).all()
        emails = sorted({(e or "").lower() for e, _ in contact_rows if e})
        name_by_email: dict[str, str | None] = {
            (e or "").lower(): n for e, n in contact_rows if e
        }

        call_rows = (
            await s.execute(
                select(CallOutcome)
                .where(CallOutcome.lead_id == lead.id, CallOutcome.outcome == "not_interested")
                .order_by(desc(CallOutcome.logged_at))
                .limit(_MAX_OBJECTIONS)
            )
        ).scalars().all()

        supp_rows: list[Suppression] = []
        if emails:
            supp_rows = list(
                (
                    await s.execute(
                        select(Suppression)
                        .where(Suppression.email.in_(emails))
                        .order_by(desc(Suppression.added_at))
                        .limit(_MAX_OBJECTIONS)
                    )
                ).scalars().all()
            )

    items: list[ObjectionItem] = []
    for c in call_rows:
        items.append(
            ObjectionItem(
                source="call",
                logged_at=_iso(c.logged_at) or "",
                text=(c.note or "").strip() or "Not interested",
                contact_name=c.logged_by,
            )
        )
    for sup in supp_rows:
        reason = (sup.reason or "do_not_contact").strip()
        items.append(
            ObjectionItem(
                source="suppression",
                logged_at=_iso(sup.added_at) or "",
                text=f"{reason} — {sup.email}",
                contact_name=name_by_email.get((sup.email or "").lower()),
            )
        )

    # Newest first across both sources, capped at 25.
    items.sort(key=lambda i: i.logged_at, reverse=True)
    return items[:_MAX_OBJECTIONS]


async def get_activity_page(
    sessionmaker: Any, broker_id: str, *, limit: int, before: datetime | None
) -> tuple[list[ActivityCallEvent | ActivityEmailEvent], bool]:
    """Return (events, has_more). The router computes the cursor from the
    last event's timestamp; we fetch ``limit + 1`` to know if more exist."""
    async with sessionmaker() as s:
        exists = (
            await s.execute(
                select(Lead.id).where(Lead.id == broker_id, Lead.kind == "Broker")
            )
        ).scalar_one_or_none()
        if exists is None:
            raise NotFoundError("broker not found")
        events = await _load_activity(s, broker_id, limit + 1, before)

    has_more = len(events) > limit
    if has_more:
        events = events[:limit]
    return events, has_more


# ---------- overview metrics (restore of pre-ba15198 /brokers surface) ----

Segment = str  # Literal["all","hot","warm","payment_issues","dormant","not_interested","neutral"]


@dataclass
class MonthlyPoint:
    month: str          # "YYYY-MM"
    booked: int
    rejected: int
    sent: int
    replied: int


@dataclass
class OverviewMetricsRow:
    """One broker's dense overview block. Nullable fields for brand-new brokers."""

    health_score: int
    health_delta: int | None
    health_thin: bool
    health_components: dict[str, float]
    win_rate: float | None
    win_rate_thin: bool
    booked_12m: int
    rejected_12m: int
    sent_30d: int
    replied_30d: int
    reply_rate: float | None
    avg_reply_hours: float | None
    tone_30d: float | None
    has_bounce: bool
    has_suppression: bool
    monthly_series: list[MonthlyPoint]
    last_contact_at: str | None
    days_since_last_contact: int | None
    segment: Segment


# Available server-side sorts (`sort=` query param). The deterministic
# tiebreaker (-fit_score, name, id) is always appended so pagination stays
# stable.
SORT_KEYS = ("health", "win_rate", "booked", "rejected", "days_since", "name")


def _month_bucket(dt: datetime) -> str:
    return dt.strftime("%Y-%m")


def _month_sequence(now: datetime, months: int = 12) -> list[str]:
    """Return the last ``months`` month-keys, oldest first, timezone-independent.

    Walks calendar months by (year, month) arithmetic — no dateutil needed.
    """
    y, m = now.year, now.month
    out: list[str] = []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            m = 12
            y -= 1
    return list(reversed(out))


def _derive_segment(
    *,
    health: int,
    next_action_kind: str,
    has_bounce: bool,
    has_suppression: bool,
    days_since: int | None,
    latest_call_outcome: str | None,
) -> Segment:
    """Rule table — matches the plan's definitions. First-match-wins.

    Order matters: not_interested + payment_issues bite first so a broker who
    said "no" or bounced doesn't get surfaced as "hot".
    """
    if latest_call_outcome == "not_interested":
        return "not_interested"
    if has_bounce or has_suppression:
        return "payment_issues"
    if days_since is not None and days_since >= 60:
        return "dormant"
    if next_action_kind in ("call", "email") and health >= 70:
        return "hot"
    if next_action_kind in ("call", "email") and 40 <= health < 70:
        return "warm"
    return "neutral"


async def get_overview_metrics(
    sessionmaker: Any,
    lead_ids: list[str],
    *,
    action_by_lead: dict[str, NextAction] | None = None,
    latest_call_by_lead: dict[str, CallOutcome | None] | None = None,
    last_activity_by_lead: dict[str, datetime | None] | None = None,
) -> dict[str, OverviewMetricsRow]:
    """Compute the overview block per broker in one trip.

    ``action_by_lead`` + ``latest_call_by_lead`` + ``last_activity_by_lead``
    are optional pre-computed inputs — if the caller already has them (the
    list route always does), we avoid a second round-trip. Otherwise this
    function does nothing fancy about reloading them; segmentation will fall
    back to "neutral" when we have no action signal.
    """
    if not lead_ids:
        return {}

    now = _now()
    year_cutoff = now - timedelta(days=WIN_RATE_WINDOW_DAYS)
    thirty_cutoff = now - timedelta(days=VOLUME_WINDOW_DAYS)
    tone_cutoff = now - timedelta(days=TONE_WINDOW_DAYS)
    months = _month_sequence(now, 12)
    month_set = set(months)

    async with sessionmaker() as s:
        # Call outcomes: group by lead + month + outcome (booked/not_interested
        # count toward win-rate; also drive the sparkline).
        call_rows = (
            await s.execute(
                select(CallOutcome.lead_id, CallOutcome.outcome, CallOutcome.logged_at)
                .where(CallOutcome.lead_id.in_(lead_ids))
                .where(CallOutcome.logged_at >= year_cutoff)
            )
        ).all()

        # Sent / replied, with reply latency for the detail-page tile.
        sent_rows = (
            await s.execute(
                select(SentLog.lead_id, SentLog.sent_at, SentLog.replied_at)
                .where(SentLog.lead_id.in_(lead_ids))
            )
        ).all()

        # Contacts for bounce + email → mail-messages join.
        contact_rows = (
            await s.execute(
                select(LeadContact.lead_id, LeadContact.email, LeadContact.pipeline_status)
                .where(LeadContact.lead_id.in_(lead_ids))
            )
        ).all()

        # Suppressions keyed off any known email.
        emails_by_lead: dict[str, set[str]] = {}
        for lid, email, _st in contact_rows:
            if email:
                emails_by_lead.setdefault(lid, set()).add(email.lower())
        all_emails = sorted({e for es in emails_by_lead.values() for e in es})
        suppressed: set[str] = set()
        if all_emails:
            suppressed = {
                e.lower() for e in (
                    await s.execute(select(Suppression.email).where(Suppression.email.in_(all_emails)))
                ).scalars().all()
            }

        # Sentiment join: MessageInsight.from_email_normalized ∈ our emails,
        # replies in TONE_WINDOW_DAYS. Falls back to joining via MailMessage
        # when the normalised field is blank (pre-0018 rows).
        tone_rows: list[tuple[str, float, datetime]] = []
        if all_emails:
            tone_rows_norm = (
                await s.execute(
                    select(MessageInsight.from_email_normalized, MessageInsight.sentiment, MessageInsight.created_at)
                    .where(MessageInsight.from_email_normalized.in_(all_emails))
                    .where(MessageInsight.created_at >= tone_cutoff)
                )
            ).all()
            tone_rows = [(e, float(s_), t) for e, s_, t in tone_rows_norm if e]

    # Aggregate booked / rejected / monthly series per lead.
    booked_12m: dict[str, int] = {lid: 0 for lid in lead_ids}
    rejected_12m: dict[str, int] = {lid: 0 for lid in lead_ids}
    monthly: dict[str, dict[str, dict[str, int]]] = {
        lid: {m: {"booked": 0, "rejected": 0, "sent": 0, "replied": 0} for m in months}
        for lid in lead_ids
    }

    for lid, outcome, logged_at in call_rows:
        if lid not in monthly:
            continue
        logged_at = _as_utc(logged_at)
        mb = _month_bucket(logged_at)
        if outcome == "booked":
            booked_12m[lid] += 1
            if mb in month_set:
                monthly[lid][mb]["booked"] += 1
        elif outcome == "not_interested":
            rejected_12m[lid] += 1
            if mb in month_set:
                monthly[lid][mb]["rejected"] += 1

    sent_30d: dict[str, int] = {lid: 0 for lid in lead_ids}
    replied_30d: dict[str, int] = {lid: 0 for lid in lead_ids}
    reply_hours_sum: dict[str, float] = {lid: 0.0 for lid in lead_ids}
    reply_hours_n: dict[str, int] = {lid: 0 for lid in lead_ids}
    for lid, sent_at, replied_at in sent_rows:
        if lid not in monthly:
            continue
        sent_at = _as_utc(sent_at)
        replied_at = _as_utc(replied_at)
        mb = _month_bucket(sent_at)
        if mb in month_set:
            monthly[lid][mb]["sent"] += 1
            if replied_at:
                monthly[lid][mb]["replied"] += 1
        if sent_at >= thirty_cutoff:
            sent_30d[lid] += 1
            if replied_at and replied_at >= thirty_cutoff:
                replied_30d[lid] += 1
        if replied_at:
            delta_h = max(0.0, (replied_at - sent_at).total_seconds() / 3600.0)
            reply_hours_sum[lid] += delta_h
            reply_hours_n[lid] += 1

    # Bounce + suppression flags.
    has_bounce: dict[str, bool] = {lid: False for lid in lead_ids}
    has_suppression: dict[str, bool] = {lid: False for lid in lead_ids}
    for lid, _email, pipeline_status in contact_rows:
        if lid in has_bounce and pipeline_status == "bounced":
            has_bounce[lid] = True
    for lid, emails in emails_by_lead.items():
        if any(e in suppressed for e in emails):
            has_suppression[lid] = True

    # Tone — join by email → lead_id.
    lead_by_email: dict[str, list[str]] = {}
    for lid, emails in emails_by_lead.items():
        for e in emails:
            lead_by_email.setdefault(e, []).append(lid)
    tone_vals: dict[str, list[float]] = {lid: [] for lid in lead_ids}
    for email, sentiment, _t in tone_rows:
        for lid in lead_by_email.get(email.lower(), ()):
            tone_vals[lid].append(sentiment)

    # Deltas — build a 30-day-prior score from the same inputs windowed back.
    # "Prior" booked/rejected = calls [t-120, t-30]; prior sent_30d = sent in
    # [t-60, t-30]; prior tone = sentiment in [t-TONE-30, t-30]; prior days-
    # since-last = days to latest activity before t-30.
    prior_cutoff_hi = now - timedelta(days=30)
    prior_cutoff_lo_year = now - timedelta(days=WIN_RATE_WINDOW_DAYS + 30)
    prior_cutoff_lo_vol = now - timedelta(days=VOLUME_WINDOW_DAYS + 30)

    prior_booked: dict[str, int] = {lid: 0 for lid in lead_ids}
    prior_rejected: dict[str, int] = {lid: 0 for lid in lead_ids}
    prior_sent_30d: dict[str, int] = {lid: 0 for lid in lead_ids}
    prior_last_contact: dict[str, datetime | None] = {lid: None for lid in lead_ids}

    for lid, outcome, logged_at in call_rows:
        if lid not in prior_booked:
            continue
        logged_at = _as_utc(logged_at)
        if prior_cutoff_lo_year <= logged_at < prior_cutoff_hi:
            if outcome == "booked":
                prior_booked[lid] += 1
            elif outcome == "not_interested":
                prior_rejected[lid] += 1
        if logged_at < prior_cutoff_hi:
            cur = prior_last_contact.get(lid)
            if cur is None or logged_at > cur:
                prior_last_contact[lid] = logged_at

    for lid, sent_at, _replied_at in sent_rows:
        if lid not in prior_sent_30d:
            continue
        sent_at = _as_utc(sent_at)
        if prior_cutoff_lo_vol <= sent_at < prior_cutoff_hi:
            prior_sent_30d[lid] += 1
        if sent_at < prior_cutoff_hi:
            cur = prior_last_contact.get(lid)
            if cur is None or sent_at > cur:
                prior_last_contact[lid] = sent_at

    prior_tone: dict[str, list[float]] = {lid: [] for lid in lead_ids}
    for email, sentiment, t in tone_rows:
        t = _as_utc(t)
        if not (now - timedelta(days=TONE_WINDOW_DAYS + 30) <= t < prior_cutoff_hi):
            continue
        for lid in lead_by_email.get(email.lower(), ()):
            prior_tone[lid].append(sentiment)

    # Build final per-lead row.
    result: dict[str, OverviewMetricsRow] = {}
    for lid in lead_ids:
        la = _as_utc((last_activity_by_lead or {}).get(lid))
        days_since: int | None = None
        if la is not None:
            days_since = max(0, (now - la).days)

        avg_tone = sum(tone_vals[lid]) / len(tone_vals[lid]) if tone_vals[lid] else None
        current = compute_health(
            HealthInputs(
                days_since_last_contact=days_since,
                booked_12m=booked_12m[lid],
                rejected_12m=rejected_12m[lid],
                avg_sentiment=avg_tone,
                sent_30d=sent_30d[lid],
            )
        )

        prior_la = prior_last_contact.get(lid)
        prior_days_since: int | None = None
        if prior_la is not None:
            prior_days_since = max(0, (prior_cutoff_hi - prior_la).days)
        prior_avg_tone = (
            sum(prior_tone[lid]) / len(prior_tone[lid]) if prior_tone[lid] else None
        )
        # Delta is None when we cannot form a prior snapshot at all.
        health_delta: int | None = None
        if prior_la is not None or prior_booked[lid] + prior_rejected[lid] > 0 or prior_sent_30d[lid] > 0:
            prior = compute_health(
                HealthInputs(
                    days_since_last_contact=prior_days_since,
                    booked_12m=prior_booked[lid],
                    rejected_12m=prior_rejected[lid],
                    avg_sentiment=prior_avg_tone,
                    sent_30d=prior_sent_30d[lid],
                )
            )
            health_delta = current.score - prior.score

        decided = booked_12m[lid] + rejected_12m[lid]
        win_rate = (booked_12m[lid] / decided) if decided > 0 else None
        reply_rate = (replied_30d[lid] / sent_30d[lid]) if sent_30d[lid] > 0 else None
        avg_reply_hours = (
            (reply_hours_sum[lid] / reply_hours_n[lid]) if reply_hours_n[lid] > 0 else None
        )

        series = [
            MonthlyPoint(
                month=m,
                booked=monthly[lid][m]["booked"],
                rejected=monthly[lid][m]["rejected"],
                sent=monthly[lid][m]["sent"],
                replied=monthly[lid][m]["replied"],
            )
            for m in months
        ]

        action = (action_by_lead or {}).get(lid)
        latest_call = (latest_call_by_lead or {}).get(lid)
        segment = _derive_segment(
            health=current.score,
            next_action_kind=action.kind if action else "wait",
            has_bounce=has_bounce[lid],
            has_suppression=has_suppression[lid],
            days_since=days_since,
            latest_call_outcome=latest_call.outcome if latest_call else None,
        )

        result[lid] = OverviewMetricsRow(
            health_score=current.score,
            health_delta=health_delta,
            health_thin=current.thin,
            health_components=current.components,
            win_rate=win_rate,
            win_rate_thin=decided < 5,
            booked_12m=booked_12m[lid],
            rejected_12m=rejected_12m[lid],
            sent_30d=sent_30d[lid],
            replied_30d=replied_30d[lid],
            reply_rate=reply_rate,
            avg_reply_hours=avg_reply_hours,
            tone_30d=avg_tone,
            has_bounce=has_bounce[lid],
            has_suppression=has_suppression[lid],
            monthly_series=series,
            last_contact_at=_iso(la),
            days_since_last_contact=days_since,
            segment=segment,
        )
    return result


# ---------- segment + sort helpers (used by the router) --------------------


def filter_by_segment(
    sort_keys: list[tuple[int, int, str, str]],
    items: list[BrokerRowData],
    overview: dict[str, OverviewMetricsRow],
    segment: str | None,
) -> tuple[list[BrokerRowData], list[tuple[int, int, str, str]]]:
    """Return (items, sort_keys) kept to the requested segment. "all"/None → pass-through."""
    if not segment or segment == "all":
        return items, sort_keys
    keep_items: list[BrokerRowData] = []
    keep_keys: list[tuple[int, int, str, str]] = []
    for row, key in zip(items, sort_keys):
        om = overview.get(row.id)
        if om is not None and om.segment == segment:
            keep_items.append(row)
            keep_keys.append(key)
    return keep_items, keep_keys


def resort(
    items: list[BrokerRowData],
    overview: dict[str, OverviewMetricsRow],
    sort: str | None,
) -> list[BrokerRowData]:
    """Re-sort by an overview-driven key. Deterministic tiebreakers: name, id."""
    if not sort or sort not in SORT_KEYS:
        return items

    def key_fn(r: BrokerRowData) -> tuple:
        om = overview.get(r.id)
        tie = ((r.name or "").lower(), r.id)
        if sort == "name":
            return (tie[0], tie[1])
        if om is None:
            # No overview data → push to the bottom.
            return (10**9, *tie)
        if sort == "health":
            return (-om.health_score, *tie)
        if sort == "win_rate":
            return (-(om.win_rate or -1.0), *tie)
        if sort == "booked":
            return (-om.booked_12m, *tie)
        if sort == "rejected":
            return (-om.rejected_12m, *tie)
        if sort == "days_since":
            return ((om.days_since_last_contact if om.days_since_last_contact is not None else 10**9), *tie)
        return tie
    return sorted(items, key=key_fn)


def segments_count(overview: dict[str, OverviewMetricsRow]) -> dict[str, int]:
    """Per-segment broker counts, used for the pill-row badges."""
    out = {k: 0 for k in ("all", "hot", "warm", "payment_issues", "dormant", "not_interested", "neutral")}
    out["all"] = len(overview)
    for om in overview.values():
        out[om.segment] = out.get(om.segment, 0) + 1
    return out
