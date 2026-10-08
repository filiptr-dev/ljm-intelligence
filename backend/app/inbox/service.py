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

from app.shared.db import AsyncSession

from app.inbox.models import MailMessage, NoReplyTracker
from app.inbox.repository import (
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
    total = await count_emails(session, intent=intent, text_query=text_query)
    rows = await fetch_emails_page(session, intent=intent, text_query=text_query, offset=offset, limit=limit)
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


async def intent_counts(session: AsyncSession) -> list[IntentCount]:
    rows = await fetch_intent_counts(session)
    return [IntentCount(intent=i, count=int(c)) for i, c in rows]


async def sentiment_buckets(session: AsyncSession) -> SentimentBuckets:
    """Sentiment distribution over inbound-only messages (same rule the demo UI used)."""
    values = [float(v or 0.0) for (v,) in await fetch_inbound_sentiments(session)]
    pos = sum(1 for v in values if v > 0.2)
    neg = sum(1 for v in values if v < -0.1)
    return SentimentBuckets(
        positive=pos, negative=neg, neutral=max(0, len(values) - pos - neg), inbound_total=len(values)
    )


async def triage_list(
    session: AsyncSession, *, urgent_only: bool = False, limit: int = 50
) -> list[TriageItem]:
    """Urgent-first list for the triage surface. Waiting-minutes derived live."""
    # SQL order is newest-first; the Python pass below re-sorts by urgency to
    # push "urgent" strings to the top (lexical sort wouldn't).
    rows = await fetch_triage_rows(session, urgent_only=urgent_only, limit=limit)
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
    rows = await fetch_no_reply_rows(session, limit=limit)
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


async def staff_performance(session: AsyncSession) -> list[StaffRow]:
    """Per-mailbox counts + a rough reply-speed median."""
    rows = await fetch_staff_rows(session)
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
    now = datetime.now(UTC)
    volume = await count_all_messages(session)
    open_threads = await count_no_reply_rows(session)
    urgent = await count_urgent_insights(session)
    negative = await count_negative_insights(session)
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


async def status_board(session: AsyncSession, *, limit: int = 200) -> list[StatusBoardRow]:
    """One row per OPEN conversation.

    Stage derivation: last-direction + no_reply_tracker presence.
      - last=in  → ``waiting_on_us`` (we owe a reply).
      - last=out → ``waiting_on_them`` (they owe a reply).
      - closed when the thread's last message is >60 days old.
    """
    rows = await fetch_status_board_rows(session)
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
        prompt = (
            f"You are LJM, a trucking carrier. Draft a short, {tone} outreach email (3-5 "
            f"sentences) to a company. Keep the signature on its own line as '— LJM'. "
            f"No preamble, no explanations — just subject + body.\n\n"
            f"Recipient: {broker}\n"
            f"Purpose: {purpose}\n"
            f"Lane: {lane or '?'}\n"
            f"Equipment: {equipment or '?'}\n"
            f"User brief: {brief or '-'}\n"
            f"Return exactly two lines, the first starting with 'Subject:'.\n"
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
    if provider is not None and getattr(provider, "kind", None) != "null":
        prompt = (
            f"Rewrite the following email in a {tone} tone. Keep every fact, "
            f"keep the length roughly the same. No preamble, no explanations — "
            f"return only the rewritten body.\n"
            f"{('User brief: ' + brief) if brief else ''}\n\n"
            f"--- original ---\n{body_text[:4000]}\n"
        )
        try:
            call = await asyncio.wait_for(
                provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
            )
            if getattr(call, "status", None) == "ok" and (call.text or "").strip():
                out = call.text.strip()
        except TimeoutError:
            _l.info("ai_rewrite: provider timeout → identity fallback")
        except Exception as exc:  # noqa: BLE001
            _l.info("ai_rewrite: provider error %s → identity fallback", type(exc).__name__)

    final = out or body_text
    html = "".join(f"<p>{p}</p>" for p in final.split("\n\n"))
    return AiDraftOut(subject="", body_text=final, body_html=html)


@dataclass
class AskOut:
    """Shape returned by :func:`ask_question`.

    Filters distilled from a natural-language question in the /emails
    "Ask" bar. All fields default to the identity-fallback values so the
    caller can treat a provider error the same way as "no useful hint".
    """

    intent: str | None
    keywords: list[str]
    sentiment: str | None  # 'positive' | 'negative' | None
    summary: str


# Narrow allow-lists so the AI can't push garbage into the DB filters.
# These mirror the intents the ingest pipeline actually produces.
_ASK_INTENTS = {
    "load_offer", "rate_request", "booked", "complaint", "payment",
    "praise", "urgent_truck", "detention", "routine",
}
_ASK_SENTIMENTS = {"positive", "negative"}


async def ask_question(
    *,
    question: str,
    provider: Any | None = None,
) -> AskOut:
    """Translate a natural-language inbox question into a filter hint.

    Reuses the ``inbox_draft_reply`` feature slot — same AI matrix row
    as ``ai_rewrite`` / ``ai_draft_compose`` (plan-gate: one slot for
    the inbox AI flows). Never raises. On provider resolve/timeout/parse
    error returns the identity-fallback: all filters null, empty summary.
    """
    import asyncio
    import json
    import logging as _log

    _l = _log.getLogger(__name__)
    fallback = AskOut(intent=None, keywords=[], sentiment=None, summary="")

    q = (question or "").strip()
    if not q:
        return fallback

    if provider is None:
        try:
            from app.config import get_settings
            from app.integrations.adapters.ai.provider import get_for

            provider = get_for("inbox_draft_reply", settings=get_settings())
        except Exception as exc:  # noqa: BLE001
            _l.info("ask_question: provider resolve failed: %s", type(exc).__name__)
            provider = None

    if provider is None or getattr(provider, "kind", None) == "null":
        return fallback

    allowed_intents = sorted(_ASK_INTENTS)
    prompt = (
        "You translate an operator's question about their freight-brokerage "
        "inbox into a strict JSON filter. The inbox has these intents: "
        f"{', '.join(allowed_intents)}. Sentiment is one of "
        "'positive','negative', or null. Return ONLY valid minified JSON "
        "with exactly these keys: intent (one of the listed intents or null), "
        "keywords (array of 0-3 short lowercase search terms, no stopwords), "
        "sentiment ('positive','negative', or null), summary (one short "
        "English sentence stating how you understood the question). "
        "Do not wrap in markdown fences.\n\n"
        f"Question: {q[:500]}\n"
    )

    try:
        call = await asyncio.wait_for(
            provider.generate_text(prompt), timeout=AI_DRAFT_TIMEOUT_S
        )
    except TimeoutError:
        _l.info("ask_question: provider timeout → identity fallback")
        return fallback
    except Exception as exc:  # noqa: BLE001
        _l.info("ask_question: provider error %s → identity fallback", type(exc).__name__)
        return fallback

    if getattr(call, "status", None) != "ok":
        return fallback
    text = (getattr(call, "text", "") or "").strip()
    if not text:
        return fallback
    # Some providers wrap JSON in ```json fences — strip defensively.
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:].strip()
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001
        _l.info("ask_question: provider returned non-JSON → identity fallback")
        return fallback
    if not isinstance(data, dict):
        return fallback

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

    return AskOut(intent=intent, keywords=keywords, sentiment=sentiment, summary=summary)


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

    New thread id = new UUID. The sender adapter resolves per the owner
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
    thread_id = f"t-{uuid4().hex[:16]}"
    result = await sender.send(
        to=to, subject=subject, body=final_text, body_html=final_html,
        thread_id=None, from_addr=mailbox, headers=send_headers,
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
