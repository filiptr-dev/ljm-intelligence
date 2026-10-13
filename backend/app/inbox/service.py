"""Inbox analysis read-side queries.

All queries are tenant-scoped at the repository boundary; on PG the RLS
policy from migration 0018 is the backstop. The functions return plain
dataclasses/dicts so the router stays a dumb serialiser.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from app.inbox.models import MailMessage, NoReplyTracker
from app.inbox.repository import (
    Mailboxes,
    _mb_where,
    count_all_messages,
    count_emails,
    count_negative_insights,
    count_no_reply_rows,
    count_urgent_insights,
    fetch_emails_page,
    fetch_inbound_sentiments,
    fetch_intent_counts,
    fetch_no_reply_rows,
    fetch_relationship_rows,
    fetch_response_time_rows,
    fetch_staff_rows,
    fetch_status_board_rows,
    fetch_thread_rows,
    fetch_triage_rows,
    find_contact_id_by_email,
    get_settings_row as _repo_get_settings_row,
)
from app.shared.db import AsyncSession

# ---- shapes ---------------------------------------------------------------


@dataclass
class EmailRow:
    message_id: str
    mailbox: str
    thread_id: str
    direction: str  # "in" | "out"
    from_addr: str
    to_addr: str
    subject: str
    body_text: str
    sent_at: datetime
    broker_name: str | None
    intent: str
    urgency: str
    sentiment: float
    confidence: float
    rate_usd: float | None
    lane_from: str | None
    lane_to: str | None
    evidence: str | None


@dataclass
class IntentCount:
    intent: str
    count: int


@dataclass
class SentimentBuckets:
    positive: int
    neutral: int
    negative: int
    inbound_total: int


@dataclass
class TriageItem:
    thread_id: str
    message_id: str
    mailbox: str
    subject: str
    from_addr: str
    broker_name: str | None
    intent: str
    urgency: str
    sentiment: float
    sent_at: datetime
    waiting_minutes: int
    snippet: str


@dataclass
class NoReplyRow:
    thread_id: str
    mailbox: str
    to_email: str
    subject: str
    we_sent_at: datetime
    days_waiting: int
    suggested_nudge: str


@dataclass
class ResponseTimeStats:
    ours_median_minutes: int | None
    theirs_median_minutes: int | None
    ours_count: int
    theirs_count: int


@dataclass
class StaffRow:
    mailbox: str
    inbound: int
    outbound: int
    reply_speed_minutes: int | None
    dropped_threads: int


@dataclass
class OverviewKPIs:
    volume_7d: int
    open_threads: int
    urgent: int
    negative: int


@dataclass
class RelationshipHealth:
    broker_domain: str
    broker_name: str | None
    health_score: int
    inbound: int
    outbound: int
    avg_sentiment: float
    last_contact_at: datetime | None
    complaints: int
    praise: int


# ---- helpers --------------------------------------------------------------


def _as_utc(dt: datetime | None) -> datetime | None:
    """SQLite tosses tz info; treat naive as UTC so arithmetic works everywhere."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _inbound_filter() -> Any:
    """Inbound = from_addr not one of our staff mailboxes.

    We model that cheaply via ``MailMessage.from_addr != MailMessage.mailbox``:
    the mailbox column IS the staff address the message was pulled from, so a
    row with ``from_addr == mailbox`` is one we sent (outbound). This is the
    same rule the triage layer uses.
    """
    return MailMessage.from_addr != MailMessage.mailbox


async def list_emails(
    session: AsyncSession,
    *,
    intent: str | None = None,
    text_query: str | None = None,
    limit: int = 40,
    offset: int = 0,
    mailbox: Mailboxes = None,
) -> tuple[list[EmailRow], int]:
    """Paginated list for the /emails page. Joins mail_messages+message_insights.

    Returns rows ordered newest-first + the total match count (for pagination).
    """
    total = await count_emails(session, intent=intent, text_query=text_query, mailbox=mailbox)
    rows = await fetch_emails_page(
        session, intent=intent, text_query=text_query, offset=offset, limit=limit, mailbox=mailbox,
    )
    out: list[EmailRow] = []
    for r in rows:
        to_addr = (r.to_addrs or [""])[0] if r.to_addrs else ""
        direction = "out" if r.from_addr == r.mailbox else "in"
        out.append(
            EmailRow(
                message_id=r.message_id, mailbox=r.mailbox, thread_id=r.thread_id,
                direction=direction, from_addr=r.from_addr, to_addr=to_addr,
                subject=r.subject or "", body_text=r.body_text or "", sent_at=r.sent_at,
                broker_name=r.broker_name, intent=r.intent, urgency=r.urgency,
                sentiment=float(r.sentiment or 0.0), confidence=float(r.confidence or 0.0),
                rate_usd=float(r.rate_usd) if r.rate_usd is not None else None,
                lane_from=r.lane_from, lane_to=r.lane_to, evidence=r.evidence,
            )
        )
    return out, total


async def intent_counts(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[IntentCount]:
    rows = await fetch_intent_counts(session, mailbox=mailbox)
    return [IntentCount(intent=i, count=int(c)) for i, c in rows]


async def sentiment_buckets(session: AsyncSession, *, mailbox: Mailboxes = None) -> SentimentBuckets:
    """Sentiment distribution over inbound-only messages (same rule the demo UI used)."""
    values = [float(v or 0.0) for (v,) in await fetch_inbound_sentiments(session, mailbox=mailbox)]
    pos = sum(1 for v in values if v > 0.2)
    neg = sum(1 for v in values if v < -0.1)
    return SentimentBuckets(
        positive=pos, negative=neg, neutral=max(0, len(values) - pos - neg), inbound_total=len(values)
    )


async def triage_list(
    session: AsyncSession, *, urgent_only: bool = False, limit: int = 50,
    mailbox: Mailboxes = None,
) -> list[TriageItem]:
    """Urgent-first list for the triage surface. Waiting-minutes derived live."""
    # SQL order is newest-first; the Python pass below re-sorts by urgency to
    # push "urgent" strings to the top (lexical sort wouldn't).
    rows = await fetch_triage_rows(session, urgent_only=urgent_only, limit=limit, mailbox=mailbox)
    now = datetime.now(UTC)
    out: list[TriageItem] = []
    for r in rows:
        sent = _as_utc(r.sent_at)
        waiting = max(0, int((now - sent).total_seconds() // 60)) if sent else 0
        snippet = (r.body_text or "")[:160]
        out.append(
            TriageItem(
                thread_id=r.thread_id, message_id=r.message_id, mailbox=r.mailbox,
                subject=r.subject or "", from_addr=r.from_addr, broker_name=r.broker_name,
                intent=r.intent, urgency=r.urgency, sentiment=float(r.sentiment or 0.0),
                sent_at=r.sent_at, waiting_minutes=waiting, snippet=snippet,
            )
        )
    # Final-pass sort: urgent first, then newest.
    out.sort(key=lambda x: (0 if x.urgency == "urgent" else 1, -_as_utc(x.sent_at).timestamp()))
    return out[:limit]


async def no_reply_list(
    session: AsyncSession, *, limit: int = 50, mailbox: Mailboxes = None,
) -> list[NoReplyRow]:
    now = datetime.now(UTC)
    rows = await fetch_no_reply_rows(session, limit=limit, mailbox=mailbox)
    out: list[NoReplyRow] = []
    for r in rows:
        we_sent = _as_utc(r.we_sent_at) or now
        days = max(0, int((now - we_sent).total_seconds() // 86400))
        if days >= 7:
            nudge = f"Follow up — it has been {days} days with no reply."
        elif days >= 3:
            nudge = f"Light nudge suggested ({days} days silent)."
        else:
            nudge = "Fresh — give it a day or two."
        out.append(
            NoReplyRow(
                thread_id=r.thread_id, mailbox=r.mailbox, to_email=r.to_email_normalized,
                subject=r.subject, we_sent_at=r.we_sent_at, days_waiting=days,
                suggested_nudge=nudge,
            )
        )
    return out


async def response_time_stats(session: AsyncSession, *, broker_domain: str | None = None) -> ResponseTimeStats:
    """Median response time for "ours" and "theirs" per thread.

    Simple pairwise walk: for each thread, order messages by sent_at, and
    compute the gap at every direction flip. ``ours`` is a gap that ends with
    an outbound message; ``theirs`` is one that ends with an inbound message.
    """
    rows = await fetch_response_time_rows(session, broker_domain=broker_domain)

    threads: dict[str, list[tuple[datetime, str]]] = defaultdict(list)
    for r in rows:
        direction = "out" if r.from_addr == r.mailbox else "in"
        threads[r.thread_id].append((_as_utc(r.sent_at), direction))

    ours_gaps: list[float] = []
    theirs_gaps: list[float] = []
    for msgs in threads.values():
        for i in range(1, len(msgs)):
            prev_t, prev_d = msgs[i - 1]
            t, d = msgs[i]
            if d == prev_d or not prev_t or not t:
                continue
            gap_min = max(0.0, (t - prev_t).total_seconds() / 60)
            if d == "out":
                ours_gaps.append(gap_min)
            else:
                theirs_gaps.append(gap_min)

    def _median(xs: list[float]) -> int | None:
        if not xs:
            return None
        xs = sorted(xs)
        mid = len(xs) // 2
        return int(xs[mid] if len(xs) % 2 else (xs[mid - 1] + xs[mid]) / 2)

    return ResponseTimeStats(
        ours_median_minutes=_median(ours_gaps),
        theirs_median_minutes=_median(theirs_gaps),
        ours_count=len(ours_gaps),
        theirs_count=len(theirs_gaps),
    )


async def staff_performance(session: AsyncSession, *, mailbox: Mailboxes = None) -> list[StaffRow]:
    """Per-mailbox counts + a rough reply-speed median."""
    rows = await fetch_staff_rows(session, mailbox=mailbox)
    bucket: dict[str, dict] = defaultdict(lambda: {"inbound": 0, "outbound": 0, "gaps": [], "dropped": 0})
    thread_last_dir: dict[tuple[str, str], tuple[datetime, str] | None] = {}
    for r in rows:
        box = r.mailbox
        direction = "out" if r.from_addr == r.mailbox else "in"
        if direction == "in":
            bucket[box]["inbound"] += 1
        else:
            bucket[box]["outbound"] += 1
        sent = _as_utc(r.sent_at)
        prev = thread_last_dir.get((box, r.thread_id))
        if prev is not None:
            pt, pd = prev
            if pd == "in" and direction == "out" and sent and pt:
                bucket[box]["gaps"].append((sent - pt).total_seconds() / 60)
        thread_last_dir[(box, r.thread_id)] = (sent, direction)

    # dropped = inbound-last threads (we never responded) per mailbox.
    for (box, _tid), last in thread_last_dir.items():
        if last and last[1] == "in":
            bucket[box]["dropped"] += 1

    out: list[StaffRow] = []
    for box, b in bucket.items():
        gaps = sorted(b["gaps"])
        median = int(gaps[len(gaps) // 2]) if gaps else None
        out.append(
            StaffRow(
                mailbox=box, inbound=b["inbound"], outbound=b["outbound"],
                reply_speed_minutes=median, dropped_threads=b["dropped"],
            )
        )
    out.sort(key=lambda r: r.inbound + r.outbound, reverse=True)
    return out


async def overview_kpis(session: AsyncSession, *, mailbox: Mailboxes = None) -> OverviewKPIs:
    now = datetime.now(UTC)
    volume = await count_all_messages(session, mailbox=mailbox)
    open_threads = await count_no_reply_rows(session, mailbox=mailbox)
    urgent = await count_urgent_insights(session, mailbox=mailbox)
    negative = await count_negative_insights(session, mailbox=mailbox)
    _ = now  # reserved for a windowed count once we add received_at filter
    return OverviewKPIs(volume_7d=volume, open_threads=open_threads, urgent=urgent, negative=negative)


async def relationship_health(session: AsyncSession, broker_domain: str) -> RelationshipHealth:
    """0–100 composite: reply-speed + sentiment + recency + complaint density.

    Deliberately simple formula so the number is explainable: we start at 50,
    nudge up for praise + fast replies, nudge down for complaints + silence.
    """
    rows = await fetch_relationship_rows(session, broker_domain=broker_domain)
    if not rows:
        return RelationshipHealth(
            broker_domain=broker_domain, broker_name=None, health_score=50,
            inbound=0, outbound=0, avg_sentiment=0.0, last_contact_at=None,
            complaints=0, praise=0,
        )

    inbound = sum(1 for r in rows if r.from_addr != r.mailbox)
    outbound = len(rows) - inbound
    avg_sent = sum(float(r.sentiment or 0) for r in rows) / len(rows)
    complaints = sum(1 for r in rows if r.intent == "complaint")
    praise = sum(1 for r in rows if r.intent == "praise")
    last = max((_as_utc(r.sent_at) for r in rows if r.sent_at), default=None)
    broker_name = next((r.broker_name for r in rows if r.broker_name), None)

    score = 50 + int(avg_sent * 20) + praise * 3 - complaints * 5
    if last is not None:
        days_since = (datetime.now(UTC) - last).days
        score -= max(0, days_since - 14) // 7  # -1 per week of silence past 2 weeks
    score = max(0, min(100, score))

    return RelationshipHealth(
        broker_domain=broker_domain, broker_name=broker_name, health_score=score,
        inbound=inbound, outbound=outbound, avg_sentiment=round(avg_sent, 3),
        last_contact_at=last, complaints=complaints, praise=praise,
    )


@dataclass
class CounterpartIdentity:
    address: str
    domain: str | None


@dataclass
class RelatedThreadRow:
    thread_id: str
    latest_subject: str
    latest_received_at: datetime
    latest_intent: str | None
    message_count: int


@dataclass
class RelatedThreadsResult:
    counterpart: CounterpartIdentity
    items: list[RelatedThreadRow]


async def latest_thread_for_lead(
    session: AsyncSession, lead_id: str,
) -> str | None:
    """Return the ``thread_id`` of the most recent mail belonging to a lead.

    Reuses :func:`app.analysis.lead_ai_summary_service.resolve_lead_match_identity`
    so the broker-page "Draft this" shortcut sees the exact same mail the
    lead summary sees — primary + contact emails plus the lead's non-free-mail
    domain, with the ambiguity filter that drops shared-with-another-lead
    identifiers. Returns ``None`` when the broker has no attributable mail.

    Raises :class:`app.analysis.lead_ai_summary_service.LeadNotFound` when
    ``lead_id`` is unknown — the router maps that to a 404.
    """
    from sqlalchemy import desc as _desc, or_ as _or_, select as _select

    from app.analysis.lead_ai_summary_service import resolve_lead_match_identity
    from app.inbox.models import MailMessage as _MM

    emails, domains = await resolve_lead_match_identity(session, lead_id)
    if not emails and not domains:
        return None
    where_clauses = []
    if emails:
        where_clauses.append(_MM.email_lower.in_(sorted(emails)))
    if domains:
        where_clauses.append(_MM.email_domain.in_(sorted(domains)))
    row = (
        await session.execute(
            _select(_MM.thread_id)
            .where(_or_(*where_clauses))
            .order_by(_desc(_MM.received_at), _desc(_MM.message_id))
            .limit(1)
        )
    ).first()
    return str(row[0]) if row is not None else None


async def list_counterpart_threads(
    session: AsyncSession, thread_id: str, *, limit: int = 50,
) -> RelatedThreadsResult | None:
    """All other threads with the counterpart on ``thread_id``.

    Counterpart = the most recent non-owner ``from_addr`` in the thread
    (``from_addr != mailbox``). Match other threads by equal
    ``email_lower`` OR equal ``email_domain`` when the domain is not a
    free-mail provider — same rule the shipper-merge pipeline uses.

    ORM-only (no ``text(...)``). ``latest_intent`` is pulled with a
    correlated scalar subquery against ``message_insights``.
    """
    from sqlalchemy import and_, desc, func as _func, or_, select as _select

    from app.inbox.models import MailMessage as _MM, MessageInsight as _MI
    from app.prospecting.pipeline.shipper_merge import FREE_MAIL_DOMAINS

    # Resolve counterpart from the current thread.
    cp_row = (
        await session.execute(
            _select(_MM.email_lower, _MM.email_domain)
            .where(_MM.thread_id == thread_id)
            .where(_MM.from_addr != _MM.mailbox)
            .order_by(desc(_MM.received_at))
            .limit(1)
        )
    ).first()
    if cp_row is None:
        return None
    cp_email = (cp_row[0] or "").strip().lower() or None
    cp_domain = (cp_row[1] or "").strip().lower() or None
    if cp_email is None and cp_domain is None:
        return None

    # Domain is useful only when it's a real company domain.
    domain_usable = (
        cp_domain is not None and cp_domain not in FREE_MAIL_DOMAINS
    )

    predicates = []
    if cp_email:
        predicates.append(_MM.email_lower == cp_email)
    if domain_usable:
        predicates.append(_MM.email_domain == cp_domain)
    if not predicates:
        return RelatedThreadsResult(
            counterpart=CounterpartIdentity(
                address=cp_email or "",
                domain=cp_domain if domain_usable else None,
            ),
            items=[],
        )

    match_clause = or_(*predicates) if len(predicates) > 1 else predicates[0]

    # Correlated subquery: latest intent for a given thread.
    insight_sq = (
        _select(_MI.intent)
        .where(_MI.thread_id == _MM.thread_id)
        .order_by(desc(_MI.created_at))
        .limit(1)
        .scalar_subquery()
    )

    # Aggregate per thread: latest subject, latest received_at, count.
    grp = (
        _select(
            _MM.thread_id.label("thread_id"),
            _func.max(_MM.received_at).label("latest_received_at"),
            _func.count().label("message_count"),
        )
        .where(_MM.thread_id != thread_id)
        .where(match_clause)
        .group_by(_MM.thread_id)
        .subquery()
    )

    # Pick the latest subject for each thread via a correlated subquery
    # (not a window) — simpler and works on sqlite too.
    subject_sq = (
        _select(_MM.subject)
        .where(_MM.thread_id == grp.c.thread_id)
        .order_by(desc(_MM.received_at))
        .limit(1)
        .scalar_subquery()
    )
    intent_sq2 = (
        _select(_MI.intent)
        .where(_MI.thread_id == grp.c.thread_id)
        .order_by(desc(_MI.created_at))
        .limit(1)
        .scalar_subquery()
    )
    _ = and_, insight_sq  # keep names referenced

    rows = (
        await session.execute(
            _select(
                grp.c.thread_id,
                subject_sq.label("latest_subject"),
                grp.c.latest_received_at,
                intent_sq2.label("latest_intent"),
                grp.c.message_count,
            )
            .order_by(desc(grp.c.latest_received_at))
            .limit(limit)
        )
    ).all()

    items = [
        RelatedThreadRow(
            thread_id=r[0],
            latest_subject=(r[1] or ""),
            latest_received_at=_as_utc(r[2]) or r[2],
            latest_intent=r[3],
            message_count=int(r[4] or 0),
        )
        for r in rows
    ]
    return RelatedThreadsResult(
        counterpart=CounterpartIdentity(
            address=cp_email or "",
            domain=cp_domain if domain_usable else None,
        ),
        items=items,
    )


async def get_thread(session: AsyncSession, thread_id: str) -> list[EmailRow]:
    """All messages in a thread (newest-last), with their insights."""
    rows = await fetch_thread_rows(session, thread_id=thread_id)
    out: list[EmailRow] = []
    for r in rows:
        to_addr = (r.to_addrs or [""])[0] if r.to_addrs else ""
        direction = "out" if r.from_addr == r.mailbox else "in"
        out.append(
            EmailRow(
                message_id=r.message_id, mailbox=r.mailbox, thread_id=r.thread_id,
                direction=direction, from_addr=r.from_addr, to_addr=to_addr,
                subject=r.subject or "", body_text=r.body_text or "", sent_at=r.sent_at,
                broker_name=r.broker_name, intent=r.intent or "routine",
                urgency=r.urgency or "normal", sentiment=float(r.sentiment or 0.0),
                confidence=float(r.confidence or 0.0),
                rate_usd=float(r.rate_usd) if r.rate_usd is not None else None,
                lane_from=r.lane_from, lane_to=r.lane_to, evidence=r.evidence,
            )
        )
    return out


# ---- status board + compose/reply + forget-contact + retention -----------

from datetime import timedelta
from uuid import uuid4

from app.analysis.models import ForgetContactAudit as _ForgetAudit
from app.inbox.repository import (
    delete_insights_by_email,
    delete_messages_by_email,
    delete_messages_past_retention,
    delete_no_reply_by_email,
    delete_no_reply_for_thread,
)


@dataclass
class StatusBoardRow:
    thread_id: str
    mailbox: str
    subject: str
    broker_name: str | None
    counterparty: str
    last_direction: str
    last_sent_at: datetime | None
    owner_user_id: str | None
    stage: str  # waiting_on_us | waiting_on_them | closed
    next_step: str
    message_count: int


async def status_board(
    session: AsyncSession, *, limit: int = 200, mailbox: Mailboxes = None,
) -> list[StatusBoardRow]:
    """One row per OPEN conversation.

    Stage derivation: last-direction + no_reply_tracker presence.
      - last=in  → ``waiting_on_us`` (we owe a reply).
      - last=out → ``waiting_on_them`` (they owe a reply).
      - closed when the thread's last message is >60 days old.
    """
    rows = await fetch_status_board_rows(session, mailbox=mailbox)
    now = datetime.now(UTC)
    by_thread: dict[str, dict] = defaultdict(
        lambda: {
            "mailbox": None, "subject": None, "last_direction": "in",
            "last_sent_at": None, "counterparty": "", "count": 0,
        }
    )
    for r in rows:
        b = by_thread[r.thread_id]
        b["mailbox"] = r.mailbox
        b["subject"] = r.subject or b["subject"] or ""
        direction = "out" if r.from_addr == r.mailbox else "in"
        b["last_direction"] = direction
        b["last_sent_at"] = r.sent_at
        b["count"] += 1
        if direction == "in":
            b["counterparty"] = r.from_addr
        else:
            tos = r.to_addrs or []
            if tos:
                b["counterparty"] = tos[0]
    out: list[StatusBoardRow] = []
    for tid, b in by_thread.items():
        last = _as_utc(b["last_sent_at"]) or now
        age_days = (now - last).days
        if age_days > 60:
            stage = "closed"
            step = f"Archived — no activity for {age_days}d."
        elif b["last_direction"] == "in":
            stage = "waiting_on_us"
            step = "Reply or triage."
        else:
            stage = "waiting_on_them"
            step = f"Follow up — silent {age_days}d." if age_days > 2 else "Waiting on broker."
        if stage == "closed":
            continue
        out.append(StatusBoardRow(
            thread_id=tid, mailbox=b["mailbox"] or "", subject=b["subject"] or "",
            broker_name=None, counterparty=b["counterparty"],
            last_direction=b["last_direction"], last_sent_at=b["last_sent_at"],
            owner_user_id=None, stage=stage, next_step=step,
            message_count=b["count"],
        ))
    out.sort(
        key=lambda r: (
            0 if r.stage == "waiting_on_us" else 1,
            -(_as_utc(r.last_sent_at).timestamp() if r.last_sent_at else 0),
        )
    )
    return out[:limit]


@dataclass
class AiDraftOut:
    subject: str
    body_text: str
    body_html: str
    # ``ai_used`` is True only when a non-null provider returned non-empty
    # text within the timeout. On the template fallback it stays False and
    # ``ai_error`` carries a short machine-friendly reason (provider_null,
    # resolve_failed, timeout, empty_response, error:<ExcType>). The two
    # fields are optional metadata — existing call sites that don't pass
    # them keep working, and only the compose endpoint surfaces them to
    # the response model today.
    ai_used: bool = False
    ai_error: str | None = None


# The provider budget was 8s, which is tighter than Gemini's typical p95
# for a 3–5 sentence draft when the service is cold or the network wobbles
# — the request would silently fall back to the stock template and the UI
# would show "stock email". 25s keeps the browser well inside Vercel's
# function limit and gives the model enough rope to answer honestly.
AI_DRAFT_TIMEOUT_S = 25.0


def _template_body(intent: str, broker: str, lane_from: str, lane_to: str, rate: float | None) -> str:
    """The old template logic, lifted so the AI path can fall back to it."""
    if intent == "load_offer":
        rate_line = f"${rate:,.0f} works on our side." if rate else "We can take this at your target rate."
        return (
            f"Hi {broker},\n\n"
            f"Thanks for the load from {lane_from} to {lane_to}. {rate_line} "
            "Can you confirm the pickup window and the shipper POC?\n\n"
            "— LJM"
        )
    if intent == "rate_request":
        return (
            f"Hi {broker},\n\n"
            f"Target on {lane_from} → {lane_to} is in your range. Send the stop details and we will quote firm within the hour.\n\n"
            "— LJM"
        )
    if intent == "urgent_truck":
        return (
            f"Hi {broker},\n\n"
            "We have equipment in the area and can cover today. Call the dispatch line to lock it in.\n\n"
            "— LJM"
        )
    if intent in ("payment", "detention"):
        return (
            f"Hi {broker},\n\n"
            "Thanks for flagging — I'll pull the paperwork and get back with the resolution today.\n\n"
            "— LJM"
        )
    if intent == "complaint":
        return (
            f"Hi {broker},\n\n"
            "I hear you. Give me the load number and I will trace it personally and come back with a fix.\n\n"
            "— LJM"
        )
    return (
        f"Hi {broker},\n\n"
        "Thanks for the note. Confirming we're on it — reply back if you need anything specific.\n\n"
        "— LJM"
    )


def _ai_draft_prompt(
    broker: str,
    intent: str,
    lane_from: str,
    lane_to: str,
    rate: float | None,
    last_body: str,
    *,
    hint: str | None = None,
    truck_context: str | None = None,
    style: str = "",
) -> str:
    """One prompt string — the operator always edits before sending.

    When ``style`` carries house-style examples, we relax the "2-4 sentence /
    stock sign-off" instruction and ask the model to imitate example length +
    sign-off instead; otherwise today's prompt is unchanged.
    """
    rate_str = f"${rate:,.0f}" if rate else "unspecified"
    opener = (
        "You are LJM, a trucking carrier. Draft a reply to the broker email below "
        "that matches the length and sign-off of the example emails in the style "
        "block. No preamble, no explanations — just the reply body."
        if style else
        "You are LJM, a trucking carrier. Draft a short, professional reply (2-4 "
        "sentences) to the broker email below. Keep the signature '— LJM' on its own line. "
        "No preamble, no explanations — just the reply body."
    )
    header = (
        f"{opener}\n\n"
        f"Broker: {broker}\n"
        f"Intent: {intent}\n"
        f"Lane: {lane_from or '?'} → {lane_to or '?'}\n"
        f"Rate: {rate_str}\n"
    )
    extras = ""
    if truck_context == "ASK_FOR_LOAD_NUMBER":
        extras += (
            "Truck context: we do not know which load the broker is asking about. "
            "Politely ask for the load / order number. "
            "Do not invent a city, state, truck number or ETA.\n"
        )
    elif truck_context:
        extras += f"Truck context (the ONLY authorised fact source for the location): {truck_context}\n"
    if hint:
        extras += f"Operator's next step: {hint}\n"
    return (
        header
        + extras
        + f"Last message:\n{last_body[:1500]}\n"
        + (style or "")
    )


async def _latest_insight_derived(
    session: AsyncSession, *, thread_id: str, message_id: str, mailbox: str
) -> tuple[str | None, str | None, str | None, list[str]]:
    """Pull ``(derived_order_no, derived_lane_from, derived_lane_to, order_nos_in_thread)``.

    First tuple-slot is the last-inbound's derived order; last slot is every
    non-null ``derived_order_no`` across the thread (latest first) — the
    truck-context flow walks that list.
    """
    from sqlalchemy import desc as _desc, select as _select

    from app.inbox.models import MessageInsight as _MI

    try:
        row = (
            await session.execute(
                _select(
                    _MI.derived_order_no,
                    _MI.derived_lane_from,
                    _MI.derived_lane_to,
                )
                .where(
                    _MI.mailbox == mailbox,
                    _MI.message_id == message_id,
                )
                .order_by(_desc(_MI.created_at))
                .limit(1)
            )
        ).first()
        order_no = row[0] if row else None
        d_lf = row[1] if row else None
        d_lt = row[2] if row else None

        thread_rows = (
            await session.execute(
                _select(_MI.derived_order_no, _MI.created_at)
                .where(_MI.thread_id == thread_id)
                .order_by(_desc(_MI.created_at))
            )
        ).all()
        order_nos = [r[0] for r in thread_rows if r[0]]
        return order_no, d_lf, d_lt, order_nos
    except Exception:  # noqa: BLE001
        return None, None, None, []


async def _build_truck_context(
    session: AsyncSession, *, order_nos: list[str]
) -> str | None:
    """Pin a load → truck for an ``urgent_truck`` draft. Returns a short
    natural-language fact line, the ``ASK_FOR_LOAD_NUMBER`` sentinel if no
    confident pin, or None on error (caller treats None like the sentinel)."""
    import logging as _log

    from sqlalchemy import or_ as _or, select as _select

    _l = _log.getLogger(__name__)
    try:
        from app.fleet.models import Truck as _Truck
        from app.prospecting.models import Load as _Load
    except Exception as exc:  # noqa: BLE001
        _l.info("_build_truck_context: model import failed: %s", type(exc).__name__)
        return None

    for order_no in order_nos:
        if not order_no:
            continue
        # Narrow candidate loads by source + source_ref, then also fall back
        # to Python-side raw JSON matching across common vendor keys.
        candidates = (
            await session.execute(
                _select(_Load).where(
                    _or(_Load.source_ref == order_no, _Load.source == "loadstop")
                ).limit(200)
            )
        ).scalars().all()
        pinned_load = None
        for ld in candidates:
            if ld.source_ref == order_no:
                pinned_load = ld
                break
            raw = ld.raw or {}
            if not isinstance(raw, dict):
                continue
            for key in ("orderNumber", "orderNo", "loadNumber", "order_no"):
                if str(raw.get(key) or "").strip() == str(order_no).strip():
                    pinned_load = ld
                    break
            if pinned_load is not None:
                break
        if pinned_load is None:
            continue
        raw = pinned_load.raw or {}
        unit_ref = None
        if isinstance(raw, dict):
            at = raw.get("assignedTruck")
            if isinstance(at, dict):
                unit_ref = at.get("number") or at.get("unitNumber")
            unit_ref = unit_ref or raw.get("truckNumber") or raw.get("unitNumber")
        unit_ref = str(unit_ref).strip() if unit_ref else ""
        if not unit_ref:
            continue
        truck = (
            await session.execute(
                _select(_Truck).where(_Truck.unit_number == unit_ref).limit(1)
            )
        ).scalar_one_or_none()
        if truck is None:
            continue
        traw = truck.raw if isinstance(truck.raw, dict) else {}
        loc = str(traw.get("locationDescription") or "").strip()
        if not loc:
            continue
        last_seen = truck.last_seen_at or truck.updated_at
        if last_seen is not None:
            try:
                ts = f" (last seen {last_seen:%Y-%m-%d %H:%M} UTC)"
            except Exception:  # noqa: BLE001
                ts = ""
        else:
            ts = ""
        return (
            f"We have truck {truck.unit_number} at {loc}{ts}. "
            f"Use this if the broker is asking about the load."
        )
    # Nothing confidently pinned.
    return "ASK_FOR_LOAD_NUMBER"


async def ai_draft_reply(
    session: AsyncSession,
    thread_id: str,
    *,
    provider: Any | None = None,
    hint: str | None = None,
) -> AiDraftOut | None:
    """Pre-fill a reply draft using the shared email builder shape.

    AI path: ask the configured provider for ``inbox_draft_reply`` and use the
    text. Template path: deterministic rules keyed off the thread's last
    inbound intent. The AI call is bounded (``AI_DRAFT_TIMEOUT_S``) and any
    error / timeout / ``no_api_key`` status falls back to the template so the
    UI never spins. ``provider`` is injectable for tests.
    """
    import asyncio
    import logging as _log

    _l = _log.getLogger(__name__)

    msgs = await get_thread(session, thread_id)
    if not msgs:
        return None
    last_in = next((m for m in reversed(msgs) if m.direction == "in"), None)
    if not last_in:
        return None
    broker = last_in.broker_name or (last_in.from_addr.split("@", 1)[0] if last_in.from_addr else "there").title()
    subject = last_in.subject if last_in.subject.lower().startswith("re:") else f"Re: {last_in.subject}"
    intent = last_in.intent or "routine"
    rate = last_in.rate_usd
    lane_from = last_in.lane_from or ""
    lane_to = last_in.lane_to or ""

    # Derived-* fallback (migration 0048) + thread-wide order numbers for the
    # urgent_truck path. One extra query; cheap and tenant-scoped by RLS.
    truck_context: str | None = None
    try:
        _, d_lf, d_lt, order_nos = await _latest_insight_derived(
            session,
            thread_id=thread_id,
            message_id=last_in.message_id,
            mailbox=last_in.mailbox,
        )
        if not lane_from and d_lf:
            lane_from = d_lf
        if not lane_to and d_lt:
            lane_to = d_lt
        if intent == "urgent_truck":
            truck_context = await _build_truck_context(session, order_nos=order_nos)
    except Exception as _exc:  # noqa: BLE001 — defensive: never break drafting.
        _l.info("ai_draft_reply: derived/truck lookup failed: %s", type(_exc).__name__)

    # House-style few-shot examples (gated by ai.match_style, default ON).
    style = ""
    try:
        from app.inbox.style_examples import build_style_block
        style = await build_style_block(
            session, to_email=last_in.from_addr, mailbox=last_in.mailbox,
        )
    except Exception as _exc:  # noqa: BLE001
        _l.info("ai_draft_reply: style helper failed: %s", type(_exc).__name__)

    # ---- AI path first; template is the fallback.
    if provider is None:
        try:
            from app.config import get_settings
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=get_settings())
        except Exception as exc:  # noqa: BLE001 — any config miss ⇒ template.
            _l.info("ai_draft_reply: provider resolve failed: %s", type(exc).__name__)
            provider = None

    ai_body: str | None = None
    ai_error: str | None = None
    if provider is None or getattr(provider, "kind", None) == "null":
        ai_error = "provider_null"
    else:
        prompt = _ai_draft_prompt(
            broker, intent, lane_from, lane_to, rate, last_in.body_text or "",
            hint=hint, truck_context=truck_context, style=style,
        )
        try:
            call = await asyncio.wait_for(
                provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
            )
            if getattr(call, "status", None) == "ok" and (call.text or "").strip():
                ai_body = call.text.strip()
            else:
                ai_error = f"empty_response:{getattr(call, 'status', None)}"
        except TimeoutError:
            ai_error = f"timeout:{AI_DRAFT_TIMEOUT_S:g}s"
            _l.info("ai_draft_reply: provider timeout → template fallback")
        except Exception as exc:  # noqa: BLE001 — any adapter error ⇒ template.
            ai_error = f"error:{type(exc).__name__}"
            _l.info("ai_draft_reply: provider error %s → template fallback", type(exc).__name__)

    if ai_body is not None:
        body = ai_body
        html = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
        return AiDraftOut(subject=subject, body_text=body, body_html=html, ai_used=True)

    # ---- Template fallback (original behaviour).
    body = _template_body(intent, broker, lane_from, lane_to, rate)
    html = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
    return AiDraftOut(subject=subject, body_text=body, body_html=html, ai_error=ai_error)


async def ai_draft_compose(
    *,
    to: str,
    purpose: str,
    tone: str,
    brief: str | None = None,
    recipient_name: str | None = None,
    lane: str | None = None,
    equipment: str | None = None,
    provider: Any | None = None,
    hint: str | None = None,
    session: AsyncSession | None = None,
) -> AiDraftOut:
    """Compose-time AI draft. Reuses the ``inbox_draft_reply`` feature slot;
    the prompt switches on ``purpose`` + ``tone`` + the optional user brief.

    Falls back to a safe template when the provider is null / errors /
    times out — same bounded pattern as ``ai_draft_reply``.
    """
    import asyncio
    import logging as _log

    _l = _log.getLogger(__name__)
    broker = (recipient_name or (to.split("@", 1)[0] if to else "there")).title()

    ai_used = False
    ai_error: str | None = None

    if provider is None:
        try:
            from app.config import get_settings
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=get_settings())
        except Exception as exc:  # noqa: BLE001
            _l.warning(
                "ai_draft_compose: provider resolve failed: %s: %s",
                type(exc).__name__, exc,
            )
            provider = None
            ai_error = f"resolve_failed:{type(exc).__name__}"

    body: str | None = None
    subject = f"{purpose.replace('_', ' ').title()} — LJM International"
    if provider is None:
        if ai_error is None:
            # Reached when the caller passed provider=None and no settings
            # error raised either (shouldn't normally happen).
            ai_error = "provider_null"
            _l.warning("ai_draft_compose: provider is None → template fallback")
    elif getattr(provider, "kind", None) == "null":
        ai_error = "provider_null"
        _l.warning(
            "ai_draft_compose: provider kind=null (no API key configured) → template fallback"
        )
    else:
        style = ""
        try:
            from app.inbox.style_examples import build_style_block
            style = await build_style_block(session, to_email=to)
        except Exception as _exc:  # noqa: BLE001
            _l.info("ai_draft_compose: style helper failed: %s", type(_exc).__name__)
        intro = (
            f"You are LJM, a trucking carrier. Draft a {tone} outreach email to a "
            f"company that matches the length and sign-off of the style examples "
            f"below. No preamble, no explanations — just subject + body."
            if style else
            f"You are LJM, a trucking carrier. Draft a short, {tone} outreach email (3-5 "
            f"sentences) to a company. Keep the signature on its own line as '— LJM'. "
            f"No preamble, no explanations — just subject + body."
        )
        prompt = (
            f"{intro}\n\n"
            f"Recipient: {broker}\n"
            f"Purpose: {purpose}\n"
            f"Lane: {lane or '?'}\n"
            f"Equipment: {equipment or '?'}\n"
            f"User brief: {brief or '-'}\n"
            f"{('Operator hint: ' + hint) if hint else ''}\n"
            f"Return exactly two lines, the first starting with 'Subject:'.\n"
            f"{style}"
        )
        try:
            call = await asyncio.wait_for(
                provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
            )
            status = getattr(call, "status", None)
            text = (getattr(call, "text", None) or "").strip()
            if status == "ok" and text:
                lines = text.splitlines()
                if lines and lines[0].lower().startswith("subject:"):
                    subject = lines[0].split(":", 1)[1].strip() or subject
                    body = "\n".join(lines[1:]).strip()
                else:
                    body = text
                if body:
                    ai_used = True
            else:
                ai_error = f"empty_response:{status}"
                _l.warning(
                    "ai_draft_compose: provider returned non-ok/empty (status=%s) → template fallback",
                    status,
                )
        except TimeoutError:
            ai_error = f"timeout:{AI_DRAFT_TIMEOUT_S:g}s"
            _l.warning(
                "ai_draft_compose: provider timeout after %ss → template fallback",
                AI_DRAFT_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001
            ai_error = f"error:{type(exc).__name__}"
            _l.warning(
                "ai_draft_compose: provider error %s: %s → template fallback",
                type(exc).__name__, exc,
            )

    if not body:
        body = (
            f"Hi {broker},\n\n"
            f"Reaching out from LJM International about {purpose.replace('_', ' ')}. "
            f"We run freight on {lane or 'your lanes'} and can usually quote within 15 minutes. "
            f"Reply with the lane + pickup window and I'll come back with a rate.\n\n"
            f"— LJM"
        )
    html = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
    return AiDraftOut(
        subject=subject, body_text=body, body_html=html,
        ai_used=ai_used, ai_error=ai_error if not ai_used else None,
    )


async def ai_rewrite(
    *,
    body_text: str,
    tone: str,
    brief: str | None = None,
    provider: Any | None = None,
    session: AsyncSession | None = None,
) -> AiDraftOut:
    """Rewrite the body in the given tone, keeping facts + ~length.

    Reuses the ``inbox_draft_reply`` feature slot (plan-gate: one AI
    feature slot for all three draft/rewrite flows). Falls back to the
    original body on any provider error.
    """
    import asyncio
    import logging as _log

    _l = _log.getLogger(__name__)
    if provider is None:
        try:
            from app.config import get_settings
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=get_settings())
        except Exception as exc:  # noqa: BLE001
            _l.info("ai_rewrite: provider resolve failed: %s", type(exc).__name__)
            provider = None

    out: str | None = None
    ai_error: str | None = None
    if provider is None or getattr(provider, "kind", None) == "null":
        ai_error = "provider_null"
    else:
        style = ""
        try:
            from app.inbox.style_examples import build_style_block
            style = await build_style_block(session, to_email=None)
        except Exception as _exc:  # noqa: BLE001
            _l.info("ai_rewrite: style helper failed: %s", type(_exc).__name__)
        voice = (
            "Rewrite the following email in LJM's voice (match the examples' "
            f"greeting, length and sign-off) and in a {tone} tone."
            if style else
            f"Rewrite the following email in a {tone} tone."
        )
        prompt = (
            f"{voice} Keep every fact, keep the length roughly the same. "
            "No preamble, no explanations — return only the rewritten body.\n"
            f"{('User brief: ' + brief) if brief else ''}\n\n"
            f"--- original ---\n{body_text[:4000]}\n"
            f"{style}"
        )
        try:
            call = await asyncio.wait_for(
                provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
            )
            if getattr(call, "status", None) == "ok" and (call.text or "").strip():
                out = call.text.strip()
            else:
                ai_error = f"empty_response:{getattr(call, 'status', None)}"
        except TimeoutError:
            ai_error = f"timeout:{AI_DRAFT_TIMEOUT_S:g}s"
            _l.info("ai_rewrite: provider timeout → identity fallback")
        except Exception as exc:  # noqa: BLE001
            ai_error = f"error:{type(exc).__name__}"
            _l.info("ai_rewrite: provider error %s → identity fallback", type(exc).__name__)

    final = out or body_text
    html = "".join(f"<p>{p}</p>" for p in final.split("\n\n"))
    return AiDraftOut(
        subject="", body_text=final, body_html=html,
        ai_used=out is not None, ai_error=None if out is not None else ai_error,
    )


@dataclass
class AskCitation:
    """One cited email the AI answer leans on."""

    thread_id: str
    subject: str
    snippet: str
    sent_at: str  # ISO-8601 for the wire
    from_addr: str
    intent: str | None
    sentiment: float | None


@dataclass
class AskOut:
    """Shape returned by :func:`ask_question`.

    The AI answers the operator's natural-language inbox question using a
    bounded slice of their real mail (subjects, snippets, intents,
    sentiment, loss reasons). ``intent`` / ``keywords`` / ``sentiment`` /
    ``summary`` keep the pre-existing filter-hint contract so the /emails
    list still narrows down to the matching rows. ``ok`` is False whenever
    we did not produce a real AI-grounded answer — then ``error`` names
    the reason and ``answer`` carries a human-readable message.
    """

    intent: str | None
    keywords: list[str]
    sentiment: str | None  # 'positive' | 'negative' | None
    summary: str
    answer: str = ""
    citations: list[AskCitation] = None  # type: ignore[assignment]
    ok: bool = False
    error: str | None = None  # 'no_question' | 'no_data' | 'ai_unavailable' | 'ai_timeout' | 'ai_error' | 'ai_parse'

    def __post_init__(self) -> None:
        if self.citations is None:
            self.citations = []


# Narrow allow-lists so the AI can't push garbage into the DB filters.
# These mirror the intents the ingest pipeline actually produces.
_ASK_INTENTS = {
    "load_offer", "rate_request", "booked", "complaint", "payment",
    "praise", "urgent_truck", "detention", "routine",
}
_ASK_SENTIMENTS = {"positive", "negative"}

# Upper bounds for the context slice we feed Gemini. These keep the prompt
# well under any model limit even on a chatty mailbox.
_ASK_CTX_ROWS = 40
_ASK_CTX_DAYS = 120
_ASK_SNIPPET_CHARS = 220


def _sanitise_chip_filter(cf: dict[str, Any] | None) -> dict[str, Any] | None:
    """Keep only the known keys from a chip's structured recipe.

    The chip filter arrives from the client (chip href → query string), so
    treat it like untrusted input: whitelist keys, coerce types, cap sizes.
    An empty dict after sanitising means "no chip" — return None so the
    retrieval falls back to the free-text keyword path.
    """
    if not isinstance(cf, dict):
        return None
    out: dict[str, Any] = {}
    bd = cf.get("broker_domain")
    if isinstance(bd, str) and 1 < len(bd) <= 253 and "@" not in bd:
        out["broker_domain"] = bd.lower().strip()
    it = cf.get("intent")
    if isinstance(it, str) and it in _ASK_INTENTS:
        out["intent"] = it
    sb = cf.get("sentiment_below")
    if isinstance(sb, int | float) and -1.0 <= float(sb) <= 1.0:
        out["sentiment_below"] = float(sb)
    for k in ("lane_from", "lane_to"):
        v = cf.get(k)
        if isinstance(v, str) and 1 <= len(v) <= 120:
            out[k] = v.strip()
    d = cf.get("days")
    if isinstance(d, int) and 1 <= d <= 365:
        out["days"] = d
    if cf.get("pending_only") is True:
        out["pending_only"] = True
    return out or None


async def _fetch_ask_context(
    session: AsyncSession, *, keywords: list[str], mailbox: Mailboxes = None,
    chip_filter: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pull a bounded slice of recent mail+insights for the AI prompt.

    Prefers inbound messages (broker -> us) because the question is almost
    always about what *they* said. If any keywords are supplied we try to
    bias toward subjects/bodies containing them; otherwise we fall back
    to the most recent window.

    When ``chip_filter`` is provided (the structured recipe behind a
    suggested chip) we replay the miner's SQL deterministically — same
    sender domain, same intent/sentiment/lane, same recency window — so
    the retrieved emails match the count the chip advertised. Without
    this, the AI often answered a question like "Review the 6 complaints
    from Epeslogistics" against a keyword LIKE on "epeslogistics" that
    missed `from@epeslogistics.com` entirely and had no intent filter,
    producing the "only 4 emails, two rate confirmations" contradiction.
    """
    from datetime import timedelta

    from sqlalchemy import and_, func, or_, select

    from app.inbox.models import MailMessage, MessageInsight, NoReplyTracker

    m = MailMessage.__table__.c
    j = MessageInsight.__table__.c
    cf = chip_filter or {}
    days = int(cf.get("days") or _ASK_CTX_DAYS)
    cutoff = datetime.now(UTC) - timedelta(days=days)

    # Pending-only (unanswered) chips are answered against the tracker rows
    # directly — not the thread's message history. Pre-fix the AI was fed
    # the broker's old rate-con / tracking-team / OOO messages inside the
    # same thread and announced "the thread has already been answered".
    # The tracker IS the source of truth for "we sent, no reply came" —
    # feed it as-is and the AI just confirms what the chip already counted.
    if cf.get("pending_only"):
        bd = (cf.get("broker_domain") or "").lower().strip()
        tracker_rows = await fetch_no_reply_rows(session, limit=_ASK_CTX_ROWS, mailbox=mailbox)
        out: list[dict[str, Any]] = []
        for tr in tracker_rows:
            to_email = (tr.to_email_normalized or "").lower()
            if bd and not to_email.endswith(f"@{bd}"):
                continue
            we_sent = tr.we_sent_at
            days_waiting = 0
            if we_sent is not None:
                try:
                    we_sent_utc = we_sent if we_sent.tzinfo else we_sent.replace(tzinfo=UTC)
                    days_waiting = max(0, int((datetime.now(UTC) - we_sent_utc).total_seconds() // 86400))
                except Exception:  # noqa: BLE001 — defensive, never let a bad row 500 Ask.
                    days_waiting = 0
            out.append({
                "thread_id": tr.thread_id,
                "subject": (tr.subject or "").strip()[:160],
                "snippet": (
                    f"we emailed {to_email or '(unknown)'} on "
                    f"{we_sent.isoformat()[:10] if we_sent else '?'}; "
                    f"{days_waiting} day(s) with no reply"
                )[:_ASK_SNIPPET_CHARS],
                "sent_at": we_sent.isoformat() if we_sent else "",
                "from_addr": tr.mailbox or "",
                "intent": "unanswered",
                "sentiment": None,
                "evidence": f"waiting_{days_waiting}d",
                "lane_from": None,
                "lane_to": None,
                "rate_usd": None,
            })
        return out[:_ASK_CTX_ROWS]

    # When the chip pins an intent / sentiment / lane we need the insight row,
    # so switch to an inner join. Otherwise keep the legacy outer join so a
    # free-text question still sees messages that lack an insight row.
    needs_insight = any(
        cf.get(k) is not None for k in ("intent", "sentiment_below", "lane_from", "lane_to")
    )
    join = (
        MailMessage.__table__.join if needs_insight else MailMessage.__table__.outerjoin
    )

    stmt = (
        select(
            m.thread_id, m.subject, m.body_text, m.sent_at, m.from_addr,
            j.intent, j.sentiment, j.evidence, j.lane_from, j.lane_to, j.rate_usd,
        )
        .select_from(
            join(
                MessageInsight.__table__,
                and_(
                    j.mailbox == m.mailbox,
                    j.message_id == m.message_id,
                    j.tenant_id == m.tenant_id,
                ),
            )
        )
        .where(m.sent_at >= cutoff)
        .order_by(m.sent_at.desc())
        .limit(_ASK_CTX_ROWS * 3)  # over-fetch, then filter in Python
    )
    if mailbox:
        stmt = stmt.where(_mb_where(m.mailbox, mailbox))

    # Inbound only — pending_only chips are already handled above by the
    # tracker-rows short-circuit, so from here on we always want the
    # broker's own mail.
    stmt = stmt.where(m.from_addr != m.mailbox)

    # Chip-driven exact filters. Keep them additive on top of the base shape.
    broker_domain = cf.get("broker_domain")
    if broker_domain and not cf.get("pending_only"):
        # Match on sender domain. For `pending_only` chips we filter by the
        # tracker's recipient domain instead (see below) because many
        # unanswered threads are first-contacts — no inbound mail at all —
        # and a sender-domain filter would wipe them out.
        stmt = stmt.where(func.lower(m.email_lower).like(f"%@{broker_domain.lower()}"))
    chip_intent = cf.get("intent")
    if chip_intent:
        stmt = stmt.where(j.intent == chip_intent)
    sentiment_below = cf.get("sentiment_below")
    if sentiment_below is not None:
        stmt = stmt.where(j.sentiment < float(sentiment_below))
    lane_from = cf.get("lane_from")
    if lane_from:
        stmt = stmt.where(j.lane_from == lane_from)
    lane_to = cf.get("lane_to")
    if lane_to:
        stmt = stmt.where(j.lane_to == lane_to)
    if cf.get("pending_only"):
        # NoReplyTracker rows are DELETED when the broker replies, so
        # "pending" is simply membership in the table. Filter the tracker
        # itself by broker-domain here so a chip about broker X only pulls
        # threads addressed to X — the message-side domain filter above is
        # bypassed because many unanswered threads have NO inbound mail.
        nr = NoReplyTracker.__table__.c
        pending = (
            select(nr.thread_id)
            .where(nr.tenant_id == m.tenant_id)
            .where(nr.mailbox == m.mailbox)
        )
        if broker_domain:
            pending = pending.where(
                func.lower(nr.to_email_normalized).like(f"%@{broker_domain.lower()}")
            )
        stmt = stmt.where(m.thread_id.in_(pending))

    if keywords and not cf:
        # Only apply keyword LIKE when there is no chip-driven recipe — a
        # structured filter is always sharper than a bag of words.
        clauses = []
        for kw in keywords[:3]:
            like = f"%{kw.lower()}%"
            clauses.append(func.lower(m.subject).like(like))
            clauses.append(func.lower(m.body_text).like(like))
        stmt = stmt.where(or_(*clauses))

    rows = list((await session.execute(stmt)).all())
    # If the keyword filter zeroed the result, drop it and retry — a blank
    # context is worse than a loosely-related one.
    # If a free-text keyword filter zeroed the result, drop it and retry — a
    # blank context is worse than a loosely-related one. Chip-driven filters
    # are intentionally NOT loosened: a chip saying "6 complaints from Epes"
    # must only answer against those exact rows, even if the set is small.
    if not rows and keywords and not cf:
        fallback = (
            select(
                m.thread_id, m.subject, m.body_text, m.sent_at, m.from_addr,
                j.intent, j.sentiment, j.evidence, j.lane_from, j.lane_to, j.rate_usd,
            )
            .select_from(
                MailMessage.__table__.outerjoin(
                    MessageInsight.__table__,
                    and_(
                        j.mailbox == m.mailbox,
                        j.message_id == m.message_id,
                        j.tenant_id == m.tenant_id,
                    ),
                )
            )
            .where(m.sent_at >= cutoff)
            .where(m.from_addr != m.mailbox)
            .order_by(m.sent_at.desc())
            .limit(_ASK_CTX_ROWS)
        )
        if mailbox:
            fallback = fallback.where(_mb_where(m.mailbox, mailbox))
        rows = list((await session.execute(fallback)).all())

    out: list[dict[str, Any]] = []
    for r in rows[:_ASK_CTX_ROWS]:
        body = (r.body_text or "").strip().replace("\n", " ")
        snippet = body[:_ASK_SNIPPET_CHARS]
        out.append({
            "thread_id": r.thread_id,
            "subject": (r.subject or "").strip()[:160],
            "snippet": snippet,
            "sent_at": r.sent_at.isoformat() if r.sent_at else "",
            "from_addr": r.from_addr or "",
            "intent": r.intent,
            "sentiment": float(r.sentiment) if r.sentiment is not None else None,
            "evidence": (r.evidence or "")[:160] if r.evidence else "",
            "lane_from": r.lane_from,
            "lane_to": r.lane_to,
            "rate_usd": float(r.rate_usd) if r.rate_usd is not None else None,
        })
    return out


def _naive_keywords(q: str) -> list[str]:
    """Cheap keyword extraction used only to narrow the mail context slice.

    Gemini still gets to rewrite ``keywords`` in the final JSON — this is
    just a pre-filter so a question like "why do we lose loads on price?"
    doesn't drown in unrelated routine mail before the AI ever sees it.
    """
    import re
    stop = {
        "the", "a", "an", "and", "or", "but", "of", "to", "in", "on", "for",
        "with", "about", "we", "us", "our", "you", "your", "do", "does", "is",
        "are", "was", "were", "be", "been", "being", "it", "that", "this",
        "these", "those", "which", "what", "why", "how", "when", "where",
        "who", "whom", "any", "all", "some", "there", "here", "not", "no",
        "lose", "lost", "losing",
    }
    toks = re.findall(r"[a-z]{3,}", q.lower())
    out: list[str] = []
    for t in toks:
        if t in stop or t in out:
            continue
        out.append(t)
        if len(out) >= 3:
            break
    return out


async def ask_question(
    *,
    question: str,
    session: AsyncSession | None = None,
    provider: Any | None = None,
    mailbox: Mailboxes = None,
    chip_filter: dict[str, Any] | None = None,
) -> AskOut:
    """Answer a natural-language inbox question with real AI over real mail.

    The question + a bounded slice of recent mail (subjects, snippets,
    intents, sentiment, extracted lane/rate/evidence) go to the
    ``inbox_draft_reply`` AI feature slot. Gemini returns JSON with a
    plain-English answer, cited example threads, and the structured
    filter hint (intent/keywords/sentiment/summary) the /emails list
    still uses to narrow down. Never raises — on resolve/timeout/parse
    error ``ok=False`` + ``error`` tells the UI exactly why.
    """
    import asyncio
    import json
    import logging as _log

    from app.shared.untrusted import FENCE_CLOSE, FENCE_OPEN, neutralize, security_instruction

    _l = _log.getLogger(__name__)

    def _fail(error: str, answer: str) -> AskOut:
        return AskOut(
            intent=None, keywords=[], sentiment=None, summary="",
            answer=answer, citations=[], ok=False, error=error,
        )

    q = (question or "").strip()
    if not q:
        return _fail("no_question", "Type a question first.")

    if provider is None:
        try:
            from app.config import get_settings
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=get_settings())
        except Exception as exc:  # noqa: BLE001
            _l.info("ask_question: provider resolve failed: %s", type(exc).__name__)
            provider = None

    if provider is None or getattr(provider, "kind", None) == "null":
        return _fail(
            "ai_unavailable",
            "The AI provider isn't configured. Add a Gemini API key in Settings to get real answers.",
        )

    # Pull real mail to ground the answer; no session means no data.
    if session is None:
        return _fail(
            "no_data",
            "There is no inbox connection to read mail from, so this can't be answered "
            "against real data.",
        )
    context_rows: list[dict[str, Any]] = []
    try:
        context_rows = await _fetch_ask_context(
            session, keywords=_naive_keywords(q), mailbox=mailbox,
            chip_filter=_sanitise_chip_filter(chip_filter),
        )
    except Exception as exc:  # noqa: BLE001
        _l.warning("ask_question: context fetch failed: %s", type(exc).__name__, exc_info=True)
        return _fail(
            "ai_error",
            "Couldn't read your mail just now (database error). Try again in a moment.",
        )

    if not context_rows:
        return _fail(
            "no_data",
            "There are no emails in the inbox yet. Connect Gmail in Settings and ingest "
            "mail — then we can answer this against the real data.",
        )

    # Build the compact context block. Each row is one short line keyed by
    # a short id (E1..EN) so Gemini can cite them without re-emitting the
    # whole thread_id in free text.
    ctx_lines: list[str] = []
    id_to_row: dict[str, dict[str, Any]] = {}
    for i, row in enumerate(context_rows, start=1):
        cid = f"E{i}"
        id_to_row[cid] = row
        parts = [
            f"intent={row['intent'] or 'unknown'}",
            f"sent={row['sent_at'][:10]}",
            f"from={neutralize(str(row['from_addr']))}",
            f"sentiment={row['sentiment']:.2f}" if row['sentiment'] is not None else "sentiment=na",
        ]
        if row.get("lane_from") or row.get("lane_to"):
            parts.append(f"lane={row.get('lane_from') or '?'}->{row.get('lane_to') or '?'}")
        if row.get("rate_usd") is not None:
            parts.append(f"rate_usd={row['rate_usd']:.0f}")
        if row.get("evidence"):
            parts.append(f"evidence={neutralize(str(row['evidence']))}")
        header = " | ".join(parts)
        ctx_lines.append(
            f"{cid}: {header}\n   subject: {neutralize(str(row['subject']))}\n"
            f"   body: {neutralize(str(row['snippet']))}"
        )
    context_block = "\n".join(ctx_lines) if ctx_lines else "(no mail available)"

    allowed_intents = sorted(_ASK_INTENTS)
    prompt = (
        "You are the operator's inbox analyst for a freight brokerage. Answer "
        "their question using ONLY the real emails provided below (do not "
        "invent brokers, rates, or dates). Be concrete: name patterns, cite "
        "evidence, and quote short phrases when it helps. If the data truly "
        "doesn't speak to the question, say so plainly.\n\n"
        "Return ONLY minified JSON with EXACTLY these keys (no markdown "
        "fences): "
        "answer (2-5 sentence natural-language reply, ≤700 chars), "
        "citations (array of 0-5 of the Ex ids above, most relevant first), "
        f"intent (one of [{', '.join(allowed_intents)}] or null — pick the "
        "intent that best narrows a follow-up email list for this question), "
        "keywords (0-3 short lowercase search terms, no stopwords, for "
        "narrowing the email list), "
        "sentiment ('positive','negative', or null), "
        "summary (one short sentence stating how you understood the "
        "question, ≤180 chars).\n\n"
        f"{security_instruction('analyse')}\n\n"
        f"Question: {q[:500]}\n\n"
        f"{FENCE_OPEN}\n"
        f"Emails ({len(ctx_lines)} most relevant):\n{context_block}\n"
        f"{FENCE_CLOSE}\n"
    )

    try:
        call = await asyncio.wait_for(
            provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
        )
    except TimeoutError:
        _l.info("ask_question: provider timeout")
        return _fail(
            "ai_timeout",
            f"The AI didn't respond within {AI_DRAFT_TIMEOUT_S:g}s. Try again in a moment.",
        )
    except Exception as exc:  # noqa: BLE001
        _l.info("ask_question: provider error %s", type(exc).__name__)
        return _fail(
            "ai_error",
            "The AI service returned an error. Check the Gemini key in Settings and try again.",
        )

    if getattr(call, "status", None) != "ok":
        err = getattr(call, "error", None) or getattr(call, "status", "error")
        return _fail("ai_error", f"The AI service is unavailable ({err}).")

    text = (getattr(call, "text", "") or "").strip()
    if not text:
        return _fail("ai_parse", "The AI returned an empty response. Try again.")
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        _l.info("ask_question: provider returned non-JSON")
        return _fail(
            "ai_parse",
            "The AI response wasn't in the expected shape. Try rewording the question.",
        )
    if not isinstance(data, dict):
        return _fail("ai_parse", "The AI response wasn't in the expected shape.")

    raw_intent = data.get("intent")
    intent = raw_intent if isinstance(raw_intent, str) and raw_intent in _ASK_INTENTS else None

    raw_kw = data.get("keywords") or []
    keywords: list[str] = []
    if isinstance(raw_kw, list):
        for k in raw_kw[:3]:
            if isinstance(k, str) and k.strip():
                keywords.append(k.strip().lower()[:40])

    raw_sent = data.get("sentiment")
    sentiment = raw_sent if isinstance(raw_sent, str) and raw_sent in _ASK_SENTIMENTS else None

    raw_summary = data.get("summary")
    summary = raw_summary.strip()[:240] if isinstance(raw_summary, str) else ""

    raw_answer = data.get("answer")
    answer = raw_answer.strip()[:1200] if isinstance(raw_answer, str) else ""
    if not answer:
        return _fail("ai_parse", "The AI didn't produce an answer. Try again.")

    raw_cites = data.get("citations") or []
    citations: list[AskCitation] = []
    if isinstance(raw_cites, list):
        for cid in raw_cites[:5]:
            if not isinstance(cid, str):
                continue
            row = id_to_row.get(cid.strip().upper())
            if not row:
                continue
            citations.append(AskCitation(
                thread_id=row["thread_id"],
                subject=row["subject"],
                snippet=row["snippet"][:200],
                sent_at=row["sent_at"],
                from_addr=row["from_addr"],
                intent=row["intent"],
                sentiment=row["sentiment"],
            ))

    return AskOut(
        intent=intent, keywords=keywords, sentiment=sentiment, summary=summary,
        answer=answer, citations=citations, ok=True, error=None,
    )


# ---- Suggested questions (Ask-your-inbox chips) ---------------------------
#
# The /emails page used to ship 3 hardcoded prompts ("Overdue invoices", …).
# The user wants concrete chips grounded in the real mailbox — named
# brokers, lanes, dates, counts. We mine the facts from SQL deterministically
# (so the LLM can never invent broker names) and compose plain-English
# templates. The whole call is wrapped so a bad miner can never 500.


@dataclass
class Suggestion:
    """One chip. ``text`` is the human-visible string; ``chip_filter`` is the
    structured, deterministic recipe the miner used to count — the Ask layer
    replays it so the retrieved emails match the count the chip shows.

    Keys honoured by :func:`_fetch_ask_context`:

    * ``broker_domain`` — sender domain substring (``m.email_lower`` endswith ``@domain``).
    * ``intent`` — one of :data:`_ASK_INTENTS`; forces inner-join + ``j.intent =``.
    * ``sentiment_below`` — numeric upper bound on ``j.sentiment``.
    * ``days`` — recency window in days (overrides the default 120d).
    * ``lane_from`` / ``lane_to`` — exact-match lane filter for rate chips.
    * ``pending_only`` — restrict to open threads in the no-reply tracker.
    """

    text: str
    kind: str
    chip_filter: dict[str, Any] = field(default_factory=dict)


# In-process TTL cache. v1 scope: no Redis, no cross-process coordination.
# A page reload after 10 min is the refresh.
_SUGGESTIONS_TTL_S = 600.0
_SUGGESTIONS_CACHE: dict[str, tuple[float, list[Suggestion]]] = {}


# Local-part prefixes that mark a sender as automated / no-reply / notification.
# A complaint miner that counts Highway monitoring alerts as "complaints from
# Highway" is wrong; a payment nudge named for a bounce-robot is wrong. We dedup
# these out of every broker-named chip. Keep the list small, single-source, and
# matched on the local-part prefix (before the first dot/plus) so variants like
# "notifications+123@" and "no.reply@" are both caught.
_AUTOMATED_LOCAL_PREFIXES: frozenset[str] = frozenset({
    "noreply", "no-reply", "no_reply", "donotreply", "do-not-reply", "do_not_reply",
    "notifications", "notification", "notify", "notice", "notices",
    "alerts", "alert", "alerting",
    "mailer-daemon", "mailerdaemon", "mailer",
    "bounce", "bounces", "postmaster", "auto-reply", "autoreply", "automated",
    "system", "support-noreply", "updates", "digest", "newsletter",
    # Case / ticketing systems that mint per-case reply addresses like
    # "cases+<token>@replies.<vendor>" — never a human broker.
    "cases", "case", "tickets", "ticket",
})

# Known notification / monitoring / vendor-automation domains whose mail is
# never from a human broker, even when the local-part looks ordinary (e.g.
# Highway's carrier-monitoring alerts, DAT's load-board notifications). Kept
# tight — expand only when a real case lands in review.
_AUTOMATED_DOMAINS: frozenset[str] = frozenset({
    "highway.com",
    "dat.com",
    "truckstop.com",
    # SaaS vendor marketing / platform notifications — never a human broker.
    "google.com",
    "googlemail.com",
    "atlassian.net",
    "slack.com",
    "notion.so",
    "linkedin.com",
})

# Bulk-mail / ESP relay suffixes — any address whose domain ends in one of
# these is a bulk sender, not a human broker (Constant Contact's
# "shared1.ccsend.com", Mailchimp's "mail.sendgrid.net", etc.).
_AUTOMATED_DOMAIN_SUFFIXES: tuple[str, ...] = (
    ".ccsend.com",
    "ccsend.com",
    ".mailchimp.com",
    ".mailchimpapp.com",
    ".sendgrid.net",
    ".mcsv.net",
    ".constantcontact.com",
    ".mktomail.com",
    ".hubspotemail.net",
    ".campaignmonitor.com",
    ".cmail19.com",
    ".cmail20.com",
    # Decisiv fleet-service case replies (replies.decisivapps.com).
    ".decisivapps.com",
)


def _domain_of(email: str | None) -> str:
    if not email:
        return ""
    at = email.find("@")
    if at == -1 or at == len(email) - 1:
        return ""
    return email[at + 1 :].lower().strip()


def _own_domain() -> str:
    """Our own company's mail domain — derived from Gmail impersonate / outreach-from.

    Used to drop "unanswered threads with Ljminternational" style chips: a
    thread with ourselves is noise, not a broker relationship.
    """
    from app.config import get_settings
    try:
        s = get_settings()
        # Prefer the mail-sending identity; fall back to outreach-from.
        src = getattr(s.gmail, "impersonate", "") or getattr(s, "outreach_from_email", "")
        return _domain_of(src)
    except Exception:  # noqa: BLE001
        return ""


def _is_free_mail(domain: str) -> bool:
    """Public free-mail provider? Reuse the single source in prospecting."""
    if not domain:
        return False
    try:
        from app.prospecting.pipeline.shipper_merge import FREE_MAIL_DOMAINS
        return domain in FREE_MAIL_DOMAINS
    except Exception:  # noqa: BLE001
        return False


def _is_automated_sender(email: str | None) -> bool:
    """True if the address is a no-reply / notification / monitoring sender.

    Checked both by local-part prefix (noreply, notifications, alerts, …) and
    by known monitoring/vendor domains (Highway, DAT, Truckstop). A chip saying
    "Review the 39 complaints from Highway" is the exact failure this guards.
    """
    if not email:
        return False
    at = email.find("@")
    if at == -1:
        return False
    local = email[:at].lower().strip()
    domain = email[at + 1 :].lower().strip()
    # Strip + and . suffixes/parts to catch "notifications+123@" or "no.reply@".
    head = local.split("+", 1)[0]
    tokens = {head, head.replace(".", "-"), head.replace(".", "")}
    if any(t in _AUTOMATED_LOCAL_PREFIXES for t in tokens):
        return True
    if domain in _AUTOMATED_DOMAINS:
        return True
    return any(domain == suf.lstrip(".") or domain.endswith(suf) for suf in _AUTOMATED_DOMAIN_SUFFIXES)


# Domains that are internal to the product/team — never freight brokers even
# when mail threads exist. xgate.io is the development agency that built LJM;
# filip.tr@xgate.io and other team addresses surface in demo / dev mailboxes
# and must never appear as broker chips.
_INTERNAL_PARTNER_DOMAINS: frozenset[str] = frozenset({
    "xgate.io",
})


def _is_excluded_broker(email: str | None) -> bool:
    """Compose: own-domain OR internal-partner OR free-mail OR automated sender → not a broker chip."""
    dom = _domain_of(email)
    if not dom:
        return True
    if dom == _own_domain():
        return True
    if dom in _INTERNAL_PARTNER_DOMAINS:
        return True
    if _is_free_mail(dom):
        return True
    return _is_automated_sender(email)


def _broker_label(email: str | None, broker_name: str | None = None) -> str:
    """Human-friendly broker label: prefer stored name, else the domain."""
    if broker_name and broker_name.strip():
        return broker_name.strip()
    if not email:
        return "Unknown"
    at = email.find("@")
    if at == -1 or at == len(email) - 1:
        return email
    dom = email[at + 1 :].lower().strip()
    # Chop the trailing TLD so "acme-logistics.com" renders as
    # "Acme-Logistics" — closer to how an operator reads it.
    head = dom.rsplit(".", 1)[0] if "." in dom else dom
    # Title-case the parts separated by - or _ but keep internal dashes.
    parts = [p.capitalize() for p in head.replace("_", "-").split("-") if p]
    return "-".join(parts) if parts else dom


def _fmt_date_short(dt: datetime | None) -> str:
    if dt is None:
        return ""
    return dt.strftime("%b %-d") if hasattr(datetime, "strftime") else dt.isoformat()[:10]


async def _mine_unanswered(session: AsyncSession, *, mailbox: Mailboxes) -> list[Suggestion]:
    """Top brokers we owe a reply to — reuse the no_reply_tracker view."""
    try:
        rows = await fetch_no_reply_rows(session, limit=200, mailbox=mailbox)
    except Exception:  # noqa: BLE001
        return []
    buckets: dict[str, list[Any]] = defaultdict(list)
    for r in rows:
        email = (r.to_email_normalized or "").lower()
        # Drop own-domain, free-mail and automated senders — none of them read
        # as "the broker we owe a reply to" in a chip.
        if _is_excluded_broker(email):
            continue
        at = email.find("@")
        key = email[at + 1 :] if at != -1 else email
        if not key:
            continue
        buckets[key].append(r)
    ranked = sorted(buckets.items(), key=lambda kv: len(kv[1]), reverse=True)
    out: list[Suggestion] = []
    for domain, group in ranked[:2]:
        n = len(group)
        if n < 1:
            continue
        broker = _broker_label(f"x@{domain}")
        oldest = min((_as_utc(r.we_sent_at) for r in group if r.we_sent_at), default=None)
        when = f" since {_fmt_date_short(oldest)}" if oldest else ""
        out.append(Suggestion(
            text=f"Which {n} thread{'s' if n != 1 else ''} with {broker}{when} {'are' if n != 1 else 'is'} still unanswered?",
            kind="unanswered",
            chip_filter={"broker_domain": domain, "pending_only": True, "days": 30},
        ))
    return out


async def _mine_insights_by_broker(
    session: AsyncSession,
    *,
    mailbox: Mailboxes,
    intent: str | None,
    sentiment_below: float | None,
    days: int,
    build_text: Any,  # (n, broker, when) -> str
    kind: str,
    limit: int = 1,
) -> list[Suggestion]:
    """Shared miner: top brokers for an insight filter in the recent window."""
    from datetime import timedelta

    from sqlalchemy import and_, func, select

    from app.inbox.models import MailMessage, MessageInsight

    m = MailMessage.__table__.c
    j = MessageInsight.__table__.c
    cutoff = datetime.now(UTC) - timedelta(days=days)

    stmt = (
        select(
            m.from_addr,
            m.email_lower,
            j.broker_name,
            func.count().label("n"),
            func.min(m.sent_at).label("earliest"),
        )
        .select_from(
            MailMessage.__table__.join(
                MessageInsight.__table__,
                and_(
                    j.mailbox == m.mailbox,
                    j.message_id == m.message_id,
                    j.tenant_id == m.tenant_id,
                ),
            )
        )
        .where(m.sent_at >= cutoff)
        .where(m.from_addr != m.mailbox)  # inbound only
        .group_by(m.from_addr, m.email_lower, j.broker_name)
        .order_by(func.count().desc())
        .limit(limit * 10)  # over-fetch so we can dedup by domain
    )
    if mailbox:
        stmt = stmt.where(_mb_where(m.mailbox, mailbox))
    if intent:
        stmt = stmt.where(j.intent == intent)
    if sentiment_below is not None:
        stmt = stmt.where(j.sentiment < sentiment_below)
    # Require a confident label on every intent/sentiment chip. The heuristic
    # fallback classifier stamps ≤0.5 on anything it couldn't match a strong
    # phrase for; keeping those out of chip counts stops OOO autoreplies and
    # rate-con noise from fuelling "complaints from <broker>" claims.
    if intent is not None or sentiment_below is not None:
        stmt = stmt.where(j.confidence >= 0.6)

    try:
        rows = list((await session.execute(stmt)).all())
    except Exception:  # noqa: BLE001
        return []

    # Collapse rows that share a domain (same broker, different senders).
    # Drop own-domain, free-mail and automated senders before collapsing — a
    # miner that names "Highway" (carrier-monitoring alerts) or "Yahoo"
    # (free-mail bucket) as a broker is the exact failure this guards.
    by_dom: dict[str, dict[str, Any]] = {}
    for r in rows:
        email = (r.email_lower or r.from_addr or "").lower()
        if _is_excluded_broker(email):
            continue
        at = email.find("@")
        dom = email[at + 1 :] if at != -1 else email
        if not dom:
            continue
        slot = by_dom.setdefault(dom, {"n": 0, "earliest": None, "broker_name": None, "email": email})
        slot["n"] += int(r.n or 0)
        earliest = r.earliest
        if earliest is not None and (slot["earliest"] is None or earliest < slot["earliest"]):
            slot["earliest"] = earliest
        if not slot["broker_name"] and r.broker_name:
            slot["broker_name"] = r.broker_name

    ranked = sorted(by_dom.items(), key=lambda kv: kv[1]["n"], reverse=True)
    out: list[Suggestion] = []
    for dom, info in ranked[:limit]:
        if info["n"] < 1:
            continue
        broker = _broker_label(info["email"], info["broker_name"])
        when = f" since {_fmt_date_short(info['earliest'])}" if info["earliest"] else ""
        chip_filter: dict[str, Any] = {"broker_domain": dom, "days": days}
        if intent:
            chip_filter["intent"] = intent
        if sentiment_below is not None:
            chip_filter["sentiment_below"] = sentiment_below
        out.append(Suggestion(
            text=build_text(info["n"], broker, when), kind=kind, chip_filter=chip_filter,
        ))
    return out


async def _mine_rate_hotspot(session: AsyncSession, *, mailbox: Mailboxes) -> list[Suggestion]:
    """Top origin→dest lane for recent rate requests."""
    from datetime import timedelta

    from sqlalchemy import and_, func, select

    from app.inbox.models import MailMessage, MessageInsight

    m = MailMessage.__table__.c
    j = MessageInsight.__table__.c
    cutoff = datetime.now(UTC) - timedelta(days=14)

    stmt = (
        select(j.lane_from, j.lane_to, func.count().label("n"))
        .select_from(
            MailMessage.__table__.join(
                MessageInsight.__table__,
                and_(
                    j.mailbox == m.mailbox,
                    j.message_id == m.message_id,
                    j.tenant_id == m.tenant_id,
                ),
            )
        )
        .where(m.sent_at >= cutoff)
        .where(m.from_addr != m.mailbox)
        .where(j.intent == "rate_request")
        .where(j.lane_from.isnot(None))
        .where(j.lane_to.isnot(None))
        .group_by(j.lane_from, j.lane_to)
        .order_by(func.count().desc())
        .limit(1)
    )
    if mailbox:
        stmt = stmt.where(_mb_where(m.mailbox, mailbox))

    try:
        row = (await session.execute(stmt)).first()
    except Exception:  # noqa: BLE001
        return []
    if not row or not row.n:
        return []
    return [Suggestion(
        text=f"Show the {row.n} rate request{'s' if row.n != 1 else ''} on {row.lane_from} → {row.lane_to} this week.",
        kind="rate_hotspot",
        chip_filter={
            "intent": "rate_request",
            "lane_from": row.lane_from,
            "lane_to": row.lane_to,
            "days": 14,
        },
    )]


async def suggest_questions(
    session: AsyncSession | None, *, mailbox: Mailboxes = None,
) -> list[Suggestion]:
    """Return 0-6 concrete chip suggestions grounded in the real mailbox.

    Deterministic grounding — every string names at least one real broker,
    lane, date or integer count from SQL. The LLM is intentionally NOT in
    the loop (plan-gate decision): a model asked to invent specifics
    hallucinates broker names, and the user's whole ask here is "not
    vague". Never raises; a failing miner is skipped silently.

    Results are cached in-process for 10 minutes per mailbox.
    """
    import logging as _log
    import time

    _l = _log.getLogger(__name__)

    key = (mailbox or "__all__").lower()
    now = time.monotonic()
    cached = _SUGGESTIONS_CACHE.get(key)
    if cached is not None and (now - cached[0]) < _SUGGESTIONS_TTL_S:
        return cached[1]

    if session is None:
        _SUGGESTIONS_CACHE[key] = (now, [])
        return []

    def _plural(n: int, word: str) -> str:
        return f"{word}" if n == 1 else f"{word}s"

    out: list[Suggestion] = []
    try:
        out.extend(await _mine_unanswered(session, mailbox=mailbox))
    except Exception as exc:  # noqa: BLE001
        _l.info("suggest_questions: unanswered miner failed: %s", type(exc).__name__)
    try:
        out.extend(await _mine_insights_by_broker(
            session, mailbox=mailbox, intent=None, sentiment_below=-0.3, days=14,
            build_text=lambda n, b, w: f"What is {b} unhappy about across {n} {_plural(n, 'email')}{w}?",
            kind="negative",
        ))
    except Exception as exc:  # noqa: BLE001
        _l.info("suggest_questions: negative miner failed: %s", type(exc).__name__)
    try:
        out.extend(await _mine_rate_hotspot(session, mailbox=mailbox))
    except Exception as exc:  # noqa: BLE001
        _l.info("suggest_questions: rate hotspot miner failed: %s", type(exc).__name__)
    try:
        out.extend(await _mine_insights_by_broker(
            session, mailbox=mailbox, intent="complaint", sentiment_below=None, days=14,
            build_text=lambda n, b, w: f"Review the {n} {_plural(n, 'complaint')} from {b}{w}.",
            kind="complaint",
        ))
    except Exception as exc:  # noqa: BLE001
        _l.info("suggest_questions: complaint miner failed: %s", type(exc).__name__)
    try:
        out.extend(await _mine_insights_by_broker(
            session, mailbox=mailbox, intent="payment", sentiment_below=None, days=30,
            build_text=lambda n, b, w: f"Which invoices from {b} are overdue? ({n} payment {_plural(n, 'email')}{w})",
            kind="payment",
        ))
    except Exception as exc:  # noqa: BLE001
        _l.info("suggest_questions: payment miner failed: %s", type(exc).__name__)

    # Dedup identical strings (one broker can land in two miners).
    cleaned: list[Suggestion] = []
    seen: set[str] = set()
    for s in out:
        if s.text in seen:
            continue
        seen.add(s.text)
        cleaned.append(s)

    # Cap at 6 to match the plan contract.
    cleaned = cleaned[:6]
    _SUGGESTIONS_CACHE[key] = (now, cleaned)
    return cleaned


def _reset_suggestions_cache() -> None:
    """Test seam — clear the in-process cache between scenarios."""
    _SUGGESTIONS_CACHE.clear()


async def _render_and_wrap(
    *,
    session: AsyncSession,
    settings,
    body_text: str,
    subject: str,
    design: Any | None,
    to: str,
) -> tuple[str, str, dict[str, str]]:
    """Build the final MIME-ready (text, html, headers) tuple.

    Delegates to ``email_render.render_email`` for the inline-styled HTML
    + plain-text alternative, and attaches the CAN-SPAM footer + the
    ``List-Unsubscribe`` headers the same way ``outreach.service.send``
    does — so inbox sends get the same compliance surface.
    """
    from app.inbox.brand import get_accent, get_brand
    from app.inbox.email_render import EmailDesignOut, render_email
    from app.outreach.unsub_config import (
        build_unsub_link,
        build_unsub_link_by_email,
        effective_unsub,
        unsub_headers as _unsub_headers,
        with_unsub_footer,
    )

    brand = await get_brand(session)

    # Design: honour the builder's picked values; fall back to the brand accent.
    design_dict: dict[str, Any] = design if isinstance(design, dict) else {}
    accent_hex = design_dict.get("accent_hex") or await get_accent(session)
    design_obj = EmailDesignOut(
        accent_hex=accent_hex,
        signature=bool(design_dict.get("signature", True)),
        logo=bool(design_dict.get("logo", True)),
        cta_label=str(design_dict.get("cta_label", "") or ""),
        cta_url=str(design_dict.get("cta_url", "") or ""),
        layout=str(design_dict.get("layout", "branded") or "branded"),
        show_truck=bool(design_dict.get("show_truck", True)),
    )

    # Unsub link — resolve a real contact by lowercased email so that
    # /unsubscribe can find a row to suppress. If no contact exists for this
    # recipient (common for free-form inbox replies), fall back to the
    # email-keyed token so the click still records a suppression keyed by
    # email, honouring CAN-SPAM / RFC 8058. Pre-fix we minted a hashed
    # pseudo-id that /unsubscribe could never resolve → 404 on click.
    row = await _repo_get_settings_row(session)
    secret, base_url = effective_unsub(settings, row)
    unsub_url = ""
    if secret and base_url:
        to_norm = (to or "").strip().lower()
        contact_id: int | None = None
        if to_norm:
            contact_id = await find_contact_id_by_email(session, email=to_norm)
        if contact_id is not None:
            unsub_url = build_unsub_link(secret, base_url, contact_id)
        elif to_norm:
            unsub_url = build_unsub_link_by_email(secret, base_url, to_norm)

    footer_text = ""
    if settings.outreach_postal_address:
        footer_text = with_unsub_footer(
            "", postal_address=settings.outreach_postal_address, unsub_url=unsub_url or "",
        ).strip()
    footer_html = ""
    if footer_text:
        # Light HTML version of the plain-text footer.
        import html as _html

        footer_html = "<br />".join(_html.escape(line) for line in footer_text.splitlines() if line.strip())

    html, text = render_email(
        body_text=body_text, subject=subject, design=design_obj, brand=brand,
        footer_html=footer_html, footer_text=footer_text,
        sender_email=(getattr(settings, "outreach_from_email", "") or "").strip(),
    )
    headers = _unsub_headers(unsub_url) if unsub_url else {}
    return text, html, headers


async def send_reply(
    session: AsyncSession,
    *,
    thread_id: str,
    body_text: str,
    body_html: str,
    settings,
    design: Any | None = None,
) -> dict:
    """Compose + send a reply inside a thread.

    One UoW: writes the outbound ``mail_messages`` row + delegates to the
    resolved sender (simulated by default; Gmail only when the owner switch
    is on). The sender's own `OwnerSwitchOff` fallback writes a simulated
    receipt — never a half-send.
    """
    msgs = await get_thread(session, thread_id)
    if not msgs:
        return {"ok": False, "reason": "thread_not_found"}
    last = msgs[-1]
    last_in = next((m for m in reversed(msgs) if m.direction == "in"), last)
    subject = last.subject if last.subject.lower().startswith("re:") else f"Re: {last.subject}"
    to = last_in.from_addr or ""
    mailbox = last.mailbox
    from app.integrations.adapters.email.sender import get_mail_sender
    from app.integrations.mail_service import effective_mode

    # CAN-SPAM gate: no postal address => no send. Mirrors outreach/service.
    if not (getattr(settings, "outreach_postal_address", "") or "").strip():
        return {"ok": False, "reason": "no_footer"}

    final_text, final_html, send_headers = await _render_and_wrap(
        session=session, settings=settings, body_text=body_text, subject=subject, design=design, to=to,
    )

    mode = await effective_mode(session, settings)
    sender = get_mail_sender(settings, mode_override=mode)
    in_reply_to = last_in.message_id if last_in else None
    references = [m.message_id for m in msgs if m.message_id]
    result = await sender.send(
        to=to, subject=subject, body=final_text, body_html=final_html,
        thread_id=thread_id, in_reply_to=in_reply_to, references=references,
        from_addr=mailbox, headers=send_headers,
    )
    # Persist as outbound mail_messages row so the thread view updates and
    # future analyses see it.
    new_msg_id = result.message_id or f"local-{uuid4().hex[:16]}"
    now = datetime.now(UTC)
    session.add(MailMessage(
        mailbox=mailbox,
        message_id=new_msg_id,
        thread_id=thread_id,
        history_id="0",
        from_addr=mailbox,
        email_lower=mailbox.lower(),
        to_addrs=[to],
        cc_addrs=[],
        subject=subject,
        sent_at=now,
        received_at=now,
        in_reply_to=in_reply_to,
        references_hdr=references,
        body_text=final_text,
        body_html=final_html,
        labels=["sent", f"mode:{result.mode}"],
        retention_until=now + timedelta(days=30 * 18),
        raw={"mode": result.mode, "simulated": result.mode == "simulated"},
    ))
    # Clear no_reply_tracker for this thread (we just replied).
    await delete_no_reply_for_thread(session, thread_id=thread_id)
    return {"ok": True, "mode": result.mode, "message_id": new_msg_id, "to": to}


async def send_new_email(
    session: AsyncSession,
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    settings,
    design: Any | None = None,
) -> dict:
    """Compose + send a brand-new outbound message (no thread parent).

    New thread id = Gmail's threadId on a real send, else a synthetic UUID. The sender adapter resolves per the owner
    switch, same as reply.
    """
    from app.integrations.adapters.email.sender import get_mail_sender
    from app.integrations.mail_service import effective_mode

    # CAN-SPAM gate: no postal address => no send. Mirrors outreach/service.
    if not (getattr(settings, "outreach_postal_address", "") or "").strip():
        return {"ok": False, "reason": "no_footer"}

    final_text, final_html, send_headers = await _render_and_wrap(
        session=session, settings=settings, body_text=body_text, subject=subject, design=design, to=to,
    )

    mode = await effective_mode(session, settings)
    sender = get_mail_sender(settings, mode_override=mode)
    mailbox = settings.outreach_from_email
    result = await sender.send(
        to=to, subject=subject, body=final_text, body_html=final_html,
        thread_id=None, from_addr=mailbox, headers=send_headers,
    )
    # A real Gmail send returns Gmail's own threadId. Store the mail under it so
    # the recipient's reply (imported by the sync under that same id) lands in
    # this conversation. The synthetic id is only for simulated/failed sends.
    thread_id = (
        result.thread_id
        if result.mode == "real" and result.thread_id
        else f"t-{uuid4().hex[:16]}"
    )
    new_msg_id = result.message_id or f"local-{uuid4().hex[:16]}"
    now = datetime.now(UTC)
    session.add(MailMessage(
        mailbox=mailbox,
        message_id=new_msg_id,
        thread_id=thread_id,
        history_id="0",
        from_addr=mailbox,
        email_lower=mailbox.lower(),
        to_addrs=[to],
        cc_addrs=[],
        subject=subject,
        sent_at=now,
        received_at=now,
        in_reply_to=None,
        references_hdr=[],
        body_text=final_text,
        body_html=final_html,
        labels=["sent", f"mode:{result.mode}"],
        retention_until=now + timedelta(days=30 * 18),
        raw={"mode": result.mode, "simulated": result.mode == "simulated"},
    ))
    # Register a no_reply_tracker row — we just sent to them.
    session.add(NoReplyTracker(
        mailbox=mailbox,
        thread_id=thread_id,
        to_email_normalized=to.lower(),
        subject=subject,
        we_sent_at=now,
    ))
    return {"ok": True, "mode": result.mode, "message_id": new_msg_id, "thread_id": thread_id}


async def forget_contact(
    session: AsyncSession, *, email: str, performed_by: str | None = None
) -> dict:
    """Delete every message + insight involving ``email`` and write an audit row."""
    email_norm = (email or "").strip().lower()
    if not email_norm or "@" not in email_norm:
        return {"ok": False, "reason": "invalid_email"}
    # messages where from_addr OR email_lower matches, OR to_addrs JSON contains the address.
    # SQL for the from side; the to-side is best-effort (JSON contains) handled on PG.
    msgs_deleted = await delete_messages_by_email(session, email=email_norm)
    insights_deleted = await delete_insights_by_email(session, email=email_norm)
    # tracker rows pointing at this address.
    await delete_no_reply_by_email(session, email=email_norm)
    session.add(_ForgetAudit(
        email_normalized=email_norm,
        messages_deleted=msgs_deleted,
        insights_deleted=insights_deleted,
        performed_by=performed_by,
        note="owner-triggered forget-contact",
    ))
    return {
        "ok": True, "email": email_norm,
        "messages_deleted": msgs_deleted,
        "insights_deleted": insights_deleted,
    }


async def retention_sweep(session: AsyncSession) -> dict:
    """Delete mail_messages whose ``retention_until`` is in the past."""
    now = datetime.now(UTC)
    deleted = await delete_messages_past_retention(session, now=now)
    return {"deleted": deleted}


# ---- Needs-reply list (plan Part C) ---------------------------------------


@dataclass
class NeedsReplyRow:
    thread_id: str
    mailbox: str
    subject: str
    from_addr: str
    broker_name: str | None
    intent: str
    urgency: str
    sent_at: datetime


_AUTO_SUBJECT_PREFIXES = (
    "auto-reply",
    "auto reply",
    "automatic reply",
    "out of office",
    "out-of-office",
    "ooo:",
)


async def fetch_needs_reply(
    session: AsyncSession,
    *,
    mailbox: Mailboxes = None,
    limit: int = 50,
) -> list[NeedsReplyRow]:
    """Threads whose newest message is INBOUND from a counterpart and that
    we haven't replied to yet. Routine-low + auto-reply / OOO are excluded.

    SQLAlchemy ORM only. The latest-message-per-thread is picked via a
    correlated subquery (same shape as fetch_related_threads) so sqlite +
    postgres both work."""
    from sqlalchemy import and_, desc as _desc, func as _func, select as _select

    from app.inbox.models import MailMessage as _MM, MessageInsight as _MI

    # Per (mailbox, thread_id) → the latest sent_at.
    latest_sq = (
        _select(
            _MM.mailbox.label("mb"),
            _MM.thread_id.label("tid"),
            _func.max(_MM.sent_at).label("latest_at"),
        )
        .group_by(_MM.mailbox, _MM.thread_id)
    )
    if mailbox:
        mb_cond = _mb_where(_func.lower(_MM.mailbox), [m.lower() for m in ([mailbox] if isinstance(mailbox, str) else mailbox)])
        if mb_cond is not None:
            latest_sq = latest_sq.where(mb_cond)
    latest_sq = latest_sq.subquery()

    # Join back to MailMessage for the latest row of each (mb, tid).
    j = (
        _select(
            _MM.thread_id,
            _MM.mailbox,
            _MM.subject,
            _MM.from_addr,
            _MM.sent_at,
            _MM.message_id,
        )
        .join(
            latest_sq,
            and_(
                _MM.mailbox == latest_sq.c.mb,
                _MM.thread_id == latest_sq.c.tid,
                _MM.sent_at == latest_sq.c.latest_at,
            ),
        )
        .order_by(_desc(_MM.sent_at))
        .limit(max(1, limit) * 3)  # over-fetch; Python filters trim noise.
    )
    rows = (await session.execute(j)).all()

    out: list[NeedsReplyRow] = []
    seen: set[tuple[str, str]] = set()
    for r in rows:
        if len(out) >= limit:
            break
        thread_id, mb, subject, from_addr, sent_at, message_id = r
        key = (mb, thread_id)
        if key in seen:
            continue
        seen.add(key)
        # Must be inbound: counterpart ≠ our mailbox.
        if (from_addr or "").strip().lower() == (mb or "").strip().lower():
            continue
        # Exclude our own domain (forwards within LJM shouldn't surface).
        if (from_addr or "").strip().lower().endswith("@ljminternational.com"):
            continue
        # Exclude auto-reply / OOO by subject.
        sub_lower = (subject or "").strip().lower()
        if any(sub_lower.startswith(p) for p in _AUTO_SUBJECT_PREFIXES):
            continue
        if sub_lower.startswith("re: ") and any(
            sub_lower[4:].startswith(p) for p in _AUTO_SUBJECT_PREFIXES
        ):
            continue
        # Look up the matching insight (if any) for this message.
        ins = (
            await session.execute(
                _select(_MI).where(
                    _MI.mailbox == mb, _MI.message_id == message_id
                ).limit(1)
            )
        ).scalar_one_or_none()
        intent = (ins.intent if ins else "routine") or "routine"
        urgency = (ins.urgency if ins else "normal") or "normal"
        broker_name = ins.broker_name if ins else None
        # Routine-low = noise.
        if intent == "routine" and urgency == "low":
            continue
        out.append(
            NeedsReplyRow(
                thread_id=thread_id,
                mailbox=mb,
                subject=subject or "",
                from_addr=from_addr or "",
                broker_name=broker_name,
                intent=intent,
                urgency=urgency,
                sent_at=sent_at,
            )
        )
    return out
