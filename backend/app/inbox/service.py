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


# ---- status board + compose/reply + forget-contact + retention -----------

from datetime import timedelta
from uuid import uuid4

from sqlalchemy import delete as _sa_delete

from app.analysis.models import ForgetContactAudit as _ForgetAudit


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


async def status_board(session: AsyncSession, *, limit: int = 200) -> list[StatusBoardRow]:
    """One row per OPEN conversation.

    Stage derivation: last-direction + no_reply_tracker presence.
      - last=in  → ``waiting_on_us`` (we owe a reply).
      - last=out → ``waiting_on_them`` (they owe a reply).
      - closed when the thread's last message is >60 days old.
    """
    rows = (
        await session.execute(
            select(
                MailMessage.thread_id, MailMessage.mailbox, MailMessage.subject,
                MailMessage.from_addr, MailMessage.to_addrs, MailMessage.sent_at,
            )
            .order_by(MailMessage.thread_id, MailMessage.sent_at.asc())
        )
    ).all()
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


AI_DRAFT_TIMEOUT_S = 8.0


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


def _ai_draft_prompt(broker: str, intent: str, lane_from: str, lane_to: str, rate: float | None, last_body: str) -> str:
    """One prompt string — the operator always edits before sending."""
    rate_str = f"${rate:,.0f}" if rate else "unspecified"
    return (
        "You are LJM, a trucking carrier. Draft a short, professional reply (2-4 "
        "sentences) to the broker email below. Keep the signature '— LJM' on its own line. "
        "No preamble, no explanations — just the reply body.\n\n"
        f"Broker: {broker}\n"
        f"Intent: {intent}\n"
        f"Lane: {lane_from or '?'} → {lane_to or '?'}\n"
        f"Rate: {rate_str}\n"
        f"Last message:\n{last_body[:1500]}\n"
    )


async def ai_draft_reply(
    session: AsyncSession,
    thread_id: str,
    *,
    provider: Any | None = None,
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
    if provider is not None and getattr(provider, "kind", None) != "null":
        prompt = _ai_draft_prompt(broker, intent, lane_from, lane_to, rate, last_in.body_text or "")
        try:
            call = await asyncio.wait_for(
                provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
            )
            if getattr(call, "status", None) == "ok" and (call.text or "").strip():
                ai_body = call.text.strip()
        except TimeoutError:
            _l.info("ai_draft_reply: provider timeout → template fallback")
        except Exception as exc:  # noqa: BLE001 — any adapter error ⇒ template.
            _l.info("ai_draft_reply: provider error %s → template fallback", type(exc).__name__)

    if ai_body is not None:
        body = ai_body
        html = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
        return AiDraftOut(subject=subject, body_text=body, body_html=html)

    # ---- Template fallback (original behaviour).
    body = _template_body(intent, broker, lane_from, lane_to, rate)
    html = "".join(f"<p>{p}</p>" for p in body.split("\n\n"))
    return AiDraftOut(subject=subject, body_text=body, body_html=html)


async def send_reply(
    session: AsyncSession,
    *,
    thread_id: str,
    body_text: str,
    body_html: str,
    settings,
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

    mode = await effective_mode(session, settings)
    sender = get_mail_sender(settings, mode_override=mode)
    in_reply_to = last_in.message_id if last_in else None
    references = [m.message_id for m in msgs if m.message_id]
    result = await sender.send(
        to=to, subject=subject, body=body_text, body_html=body_html,
        thread_id=thread_id, in_reply_to=in_reply_to, references=references,
        from_addr=mailbox,
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
        body_text=body_text,
        body_html=body_html,
        labels=["sent", f"mode:{result.mode}"],
        retention_until=now + timedelta(days=30 * 18),
        raw={"mode": result.mode, "simulated": result.mode == "simulated"},
    ))
    # Clear no_reply_tracker for this thread (we just replied).
    await session.execute(
        _sa_delete(NoReplyTracker).where(NoReplyTracker.thread_id == thread_id)
    )
    return {"ok": True, "mode": result.mode, "message_id": new_msg_id, "to": to}


async def send_new_email(
    session: AsyncSession,
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    settings,
) -> dict:
    """Compose + send a brand-new outbound message (no thread parent).

    New thread id = new UUID. The sender adapter resolves per the owner
    switch, same as reply.
    """
    from app.integrations.adapters.email.sender import get_mail_sender
    from app.integrations.mail_service import effective_mode

    mode = await effective_mode(session, settings)
    sender = get_mail_sender(settings, mode_override=mode)
    mailbox = settings.outreach_from_email
    thread_id = f"t-{uuid4().hex[:16]}"
    result = await sender.send(
        to=to, subject=subject, body=body_text, body_html=body_html,
        thread_id=None, from_addr=mailbox,
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
        body_text=body_text,
        body_html=body_html,
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
    msgs_result = await session.execute(
        _sa_delete(MailMessage).where(
            (MailMessage.email_lower == email_norm)
            | (func.lower(MailMessage.from_addr) == email_norm)
        )
    )
    msgs_deleted = msgs_result.rowcount or 0
    ins_result = await session.execute(
        _sa_delete(MessageInsight).where(MessageInsight.from_email_normalized == email_norm)
    )
    insights_deleted = ins_result.rowcount or 0
    # tracker rows pointing at this address.
    await session.execute(
        _sa_delete(NoReplyTracker).where(NoReplyTracker.to_email_normalized == email_norm)
    )
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
    result = await session.execute(
        _sa_delete(MailMessage).where(
            MailMessage.retention_until.isnot(None),
            MailMessage.retention_until < now,
        )
    )
    return {"deleted": result.rowcount or 0}
