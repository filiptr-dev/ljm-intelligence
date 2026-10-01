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
    SentLog,
    Suppression,
)
from app.pipeline.broker_next_action import (
    PRIORITY,
    NextAction,
    NextActionInput,
    compute,
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
class BrokerDetailResult:
    broker: BrokerRowData
    address: ContactField
    linkedin_company_url: str | None
    website_url: str | None
    contacts: list[NamedContactRow]
    activity: list[ActivityCallEvent | ActivityEmailEvent]
    summary: SummaryRow


# ---------- helpers ---------------------------------------------------------


def _now() -> datetime:
    return datetime.now(UTC)


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
    return BrokerListResult(
        items=[t[3] for t in enriched],
        total=len(enriched),
        sort_keys=[(t[0], t[1], t[2], t[4]) for t in enriched],
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

    return BrokerDetailResult(
        broker=broker_row,
        address=_field(lead.address, lead.address_source),
        linkedin_company_url=lead.linkedin_company_url,
        website_url=lead.website_url,
        contacts=contacts,
        activity=activity,
        summary=summary,
    )


# ---------- activity (paged) -----------------------------------------------


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
