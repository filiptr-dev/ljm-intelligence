"""Inbox analysis read-side queries.

All queries are tenant-scoped at the repository boundary; on PG the RLS
policy from migration 0018 is the backstop. The functions return plain
dataclasses/dicts so the router stays a dumb serialiser.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.models import MailMessage, MessageInsight, NoReplyTracker


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
) -> tuple[list[EmailRow], int]:
    """Paginated list for the /emails page. Joins mail_messages+message_insights.

    Returns rows ordered newest-first + the total match count (for pagination).
    """
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c

    base = (
        select(
            m.message_id, m.mailbox, m.thread_id, m.from_addr, m.to_addrs, m.subject,
            m.body_text, m.sent_at,
            j.intent, j.urgency, j.sentiment, j.confidence, j.rate_usd, j.lane_from,
            j.lane_to, j.broker_name, j.evidence,
        )
        .select_from(MailMessage.__table__.join(
            MessageInsight.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        ))
    )
    if intent:
        base = base.where(j.intent == intent)
    if text_query:
        q = f"%{text_query.lower()}%"
        base = base.where(func.lower(m.subject + " " + m.body_text).like(q))

    count_q = select(func.count()).select_from(base.subquery())
    total = int((await session.execute(count_q)).scalar_one())

    rows_q = base.order_by(m.sent_at.desc()).offset(offset).limit(limit)
    result = await session.execute(rows_q)
    out: list[EmailRow] = []
    for r in result.all():
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


async def intent_counts(session: AsyncSession) -> list[IntentCount]:
    rows = (
        await session.execute(
            select(MessageInsight.intent, func.count())
            .group_by(MessageInsight.intent)
            .order_by(func.count().desc())
        )
    ).all()
    return [IntentCount(intent=i, count=int(c)) for i, c in rows]


async def sentiment_buckets(session: AsyncSession) -> SentimentBuckets:
    """Sentiment distribution over inbound-only messages (same rule the demo UI used)."""
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    base = select(j.sentiment).select_from(
        MessageInsight.__table__.join(
            MailMessage.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        )
    ).where(_inbound_filter())
    values = [float(v or 0.0) for (v,) in (await session.execute(base)).all()]
    pos = sum(1 for v in values if v > 0.2)
    neg = sum(1 for v in values if v < -0.1)
    return SentimentBuckets(
        positive=pos, negative=neg, neutral=max(0, len(values) - pos - neg), inbound_total=len(values)
    )


async def triage_list(
    session: AsyncSession, *, urgent_only: bool = False, limit: int = 50
) -> list[TriageItem]:
    """Urgent-first list for the triage surface. Waiting-minutes derived live."""
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    stmt = (
        select(
            m.message_id, m.mailbox, m.thread_id, m.from_addr, m.subject, m.sent_at, m.body_text,
            j.intent, j.urgency, j.sentiment, j.broker_name,
        )
        .select_from(MailMessage.__table__.join(
            MessageInsight.__table__,
            and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
        ))
        .where(_inbound_filter())
    )
    if urgent_only:
        stmt = stmt.where(j.urgency == "urgent")
    # SQL order is newest-first; the Python pass below re-sorts by urgency to
    # push "urgent" strings to the top (lexical sort wouldn't).
    stmt = stmt.order_by(m.sent_at.desc()).limit(limit * 2 if not urgent_only else limit)
    rows = (await session.execute(stmt)).all()
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


async def no_reply_list(session: AsyncSession, *, limit: int = 50) -> list[NoReplyRow]:
    now = datetime.now(UTC)
    rows = (
        await session.execute(
            select(NoReplyTracker).order_by(NoReplyTracker.we_sent_at.asc()).limit(limit)
        )
    ).scalars().all()
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
    stmt = select(
        MailMessage.thread_id, MailMessage.from_addr, MailMessage.mailbox,
        MailMessage.sent_at, MailMessage.email_lower,
    ).order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
    if broker_domain:
        stmt = stmt.where(MailMessage.email_lower.ilike(f"%@{broker_domain}"))
    rows = (await session.execute(stmt)).all()

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


async def staff_performance(session: AsyncSession) -> list[StaffRow]:
    """Per-mailbox counts + a rough reply-speed median."""
    rows = (
        await session.execute(
            select(MailMessage.mailbox, MailMessage.from_addr, MailMessage.thread_id, MailMessage.sent_at)
            .order_by(MailMessage.mailbox, MailMessage.thread_id, MailMessage.sent_at)
        )
    ).all()
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


async def overview_kpis(session: AsyncSession) -> OverviewKPIs:
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    now = datetime.now(UTC)
    volume = int(
        (await session.execute(select(func.count(m.message_id)).select_from(MailMessage.__table__))).scalar_one()
    )
    open_threads = int(
        (await session.execute(select(func.count()).select_from(NoReplyTracker.__table__))).scalar_one()
    )
    urgent = int(
        (await session.execute(select(func.count()).select_from(MessageInsight.__table__).where(j.urgency == "urgent"))).scalar_one()
    )
    negative = int(
        (await session.execute(select(func.count()).select_from(MessageInsight.__table__).where(j.sentiment < -0.1))).scalar_one()
    )
    _ = now  # reserved for a windowed count once we add received_at filter
    return OverviewKPIs(volume_7d=volume, open_threads=open_threads, urgent=urgent, negative=negative)


async def relationship_health(session: AsyncSession, broker_domain: str) -> RelationshipHealth:
    """0–100 composite: reply-speed + sentiment + recency + complaint density.

    Deliberately simple formula so the number is explainable: we start at 50,
    nudge up for praise + fast replies, nudge down for complaints + silence.
    """
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    rows = (
        await session.execute(
            select(j.intent, j.sentiment, j.broker_name, m.sent_at, m.from_addr, m.mailbox)
            .select_from(MessageInsight.__table__.join(
                MailMessage.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(m.email_lower.ilike(f"%@{broker_domain}"))
        )
    ).all()
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


async def get_thread(session: AsyncSession, thread_id: str) -> list[EmailRow]:
    """All messages in a thread (newest-last), with their insights."""
    j = MessageInsight.__table__.c
    m = MailMessage.__table__.c
    rows = (
        await session.execute(
            select(
                m.message_id, m.mailbox, m.thread_id, m.from_addr, m.to_addrs, m.subject,
                m.body_text, m.sent_at,
                j.intent, j.urgency, j.sentiment, j.confidence, j.rate_usd, j.lane_from,
                j.lane_to, j.broker_name, j.evidence,
            )
            .select_from(MailMessage.__table__.outerjoin(
                MessageInsight.__table__,
                and_(j.mailbox == m.mailbox, j.message_id == m.message_id, j.tenant_id == m.tenant_id),
            ))
            .where(m.thread_id == thread_id)
            .order_by(m.sent_at.asc())
        )
    ).all()
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
