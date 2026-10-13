"""Triage on ingest — intent + urgency + sentiment in ONE call.

Design:
  * One function (`triage_message`) called inline from the ingest upsert
    path for every newly-stored message. Idempotent on
    (tenant_id, mailbox, message_id, model_version) via the unique
    constraint on ``message_insights``.
  * Demo path: if the message's ``raw["demo"]`` carries intent + sentiment
    (the simulated corpus does), use those labels verbatim. One AI call
    worth of info without hitting Gemini — tests stay fast & deterministic.
  * AI path (not exercised in tests this pass): swap in
    ``AIProviderPort.complete("inbox_triage", prompt)`` and parse the JSON.
    Shaped-but-dormant — the demo path covers the real demo today.
  * Side effects after insight is persisted:
      - ``intent == "load_offer"`` → upsert into ``loads`` with
        ``source='inbox'``, ``source_ref='<mailbox>:<message_id>'``.
      - signature regex over body_text → maybe upsert ``lead_contacts``
        if a lead exists by domain.
      - inbound message on a thread wipes any ``no_reply_tracker`` row;
        outbound inserts/refreshes one.

Why inline, not a procrastinate job (per plan "If the queue isn't ready yet"):
the queue is live but the per-message triage is tiny (microseconds against
the demo labels, one HTTP call against Gemini). Keeping it inline makes the
ingest batch's transaction cover the insight write too — a half-written
analysis is impossible, which is the whole point of the UoW rule in the plan.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.inbox.models import MessageInsight, NoReplyTracker
from app.integrations.adapters.email.mailbox import RawMessage

log = logging.getLogger(__name__)

MODEL_NAME = "demo"
MODEL_VERSION = "demo-v1"

# Intent taxonomy — stays in sync with the simulated corpus + the frontend's
# `Intent` union in src/lib/ai/types.ts. The AI path (not yet wired) returns
# the same strings.
VALID_INTENTS = {
    "load_offer",
    "rate_request",
    "urgent_truck",
    "complaint",
    "payment",
    "detention",
    "praise",
    "booked",
    "routine",
}
URGENT_INTENTS = {"urgent_truck", "complaint", "detention"}


@dataclass
class TriageOutcome:
    intent: str
    urgency: str  # "urgent" | "normal" | "low"
    sentiment: float  # -1.0 … 1.0
    confidence: float  # 0 … 1
    broker_name: str | None = None
    lane_from: str | None = None
    lane_to: str | None = None
    equipment: str | None = None
    rate_usd: float | None = None
    evidence: str | None = None


# ---- Real-mail classifier helpers -----------------------------------------
#
# The simulated corpus carries labels in ``raw["demo"]``; everything else
# falls to the rules below. The earlier fallback matched bare substrings
# like "invoice" / "issue" which turned rate-confirmation + tracking +
# OOO auto-reply mail into fake complaint / payment / negative chips.
# Word-boundary regex + an auto-reply / rate-con / tracking short-circuit
# fixes that at the source.

_AUTO_REPLY_SUBJECT_RE = re.compile(
    r"^\s*(re:\s*)?("
    r"automatic reply|auto[- ]?reply|out[- ]of[- ]office|ooo[:\-]|"
    r"on vacation|away from the office|out of the office"
    r")",
    re.IGNORECASE,
)
_AUTO_REPLY_HEADER_KEYS = frozenset({
    "auto-submitted", "x-autoreply", "x-autorespond", "x-autoresponder",
})
_AUTO_REPLY_LABELS = frozenset({"autoreply", "auto-reply", "auto_reply"})

# Rate / load / booking confirmations and tracking-team notifications are
# operational mail — never complaints, never payment chasers.
_RATE_CONF_RE = re.compile(
    r"\b(rate confirmation|load confirmation|carrier confirmation|"
    r"booking confirmation|rate (and|&) load confirmation|load tender)\b"
    r"|rate[- ]?con(firmation)?(?:\s|:|#|$)",
    re.IGNORECASE,
)
_TRACKING_RE = re.compile(
    r"\b(driveview|project ?44|p ?44|tracking team|"
    r"please advise driver|driver['’]s current location|activate (p44|tracking)|"
    r"pickup eta|eld compliance|macropoint|fourkites)\b",
    re.IGNORECASE,
)

# Strong-phrase intent patterns. Each one must be unambiguous enough that
# matching alone is good evidence — a bare "invoice" or "issue" wouldn't be.
_URGENT_RE = re.compile(
    r"\b(urgent|asap|right now|immediately|need (a )?truck today|"
    r"live load now|emergency|covering today)\b",
    re.IGNORECASE,
)
_COMPLAINT_RE = re.compile(
    # First-person phrasing only — a sales pitch mentioning that "customers
    # get frustrated by poor data" shouldn't light up the complaint miner.
    r"\b("
    r"not acceptable|unacceptable|"
    r"(i am|i'm|we are|we're) (very )?(disappointed|frustrated|unhappy|upset)|"
    r"filing a complaint|lodge a complaint|formal complaint|"
    r"this is wrong|refuse to pay|unprofessional service|"
    r"we have a (serious )?problem|not what we agreed|"
    r"damaged load|misdeliver\w+"
    r")\b",
    re.IGNORECASE,
)
_PAYMENT_RE = re.compile(
    r"\b(past due|overdue (invoice|balance|payment)|invoice (overdue|unpaid|past due)|"
    r"unpaid invoice|haven'?t received payment|not been paid|late payment|"
    r"please remit|outstanding (balance|invoice)|aging report|"
    r"invoice.*unpaid|invoice.*past due)\b",
    re.IGNORECASE,
)
_DETENTION_RE = re.compile(
    r"\b(detention charges?|layover charges?|dwell time charges?|detention invoice)\b",
    re.IGNORECASE,
)
_PRAISE_RE = re.compile(
    r"\b(great job|nice work|awesome job|thanks for the great|appreciate (your|the) help)\b",
    re.IGNORECASE,
)
_BOOKED_RE = re.compile(
    r"\b(you'?re covered|load is booked|we booked|all set for pickup|covered on this load)\b",
    re.IGNORECASE,
)
_LOAD_OFFER_RE = re.compile(
    r"\b(load offer|new load|load from .* to |target rate|load tender for|"
    r"do you have (a )?truck|got a load|covering this load)\b",
    re.IGNORECASE,
)
_RATE_REQ_RE = re.compile(
    r"\b(what rate|rate quote|quote (for|on) a load|price (for|on) this|"
    r"send (us )?a rate|need a rate on)\b",
    re.IGNORECASE,
)


def _is_autoreply(msg: RawMessage, *, subject: str, labels: set[str]) -> bool:
    """True iff the message is an OOO / autoresponder / vacation reply."""
    if _AUTO_REPLY_SUBJECT_RE.search(subject or ""):
        return True
    if labels & _AUTO_REPLY_LABELS:
        return True
    raw = msg.raw or {}
    headers = raw.get("headers") if isinstance(raw.get("headers"), dict) else None
    if headers:
        for k, v in headers.items():
            kl = str(k).lower()
            if kl in _AUTO_REPLY_HEADER_KEYS:
                return True
            if kl == "precedence" and str(v).lower() in {"auto_reply", "bulk", "list"}:
                return True
    return bool(raw.get("auto_submitted") or raw.get("auto_reply"))


def _classify(msg: RawMessage) -> TriageOutcome:
    """Pure classifier.

    * Simulated corpus: ``raw["demo"]`` carries real labels.
    * Everything else: a short-circuit for auto-replies, rate confirmations
      and tracking notices (they are NOT complaints or payment issues),
      then word-boundary regex rules. Unknown ⇒ ``routine`` with low
      confidence so chip miners can filter them out.
    """
    demo = (msg.raw or {}).get("demo") or {}
    if demo and demo.get("intent") in VALID_INTENTS:
        intent = demo["intent"]
        sentiment = float(demo.get("sentiment", 0.0))
        return TriageOutcome(
            intent=intent,
            urgency="urgent" if intent in URGENT_INTENTS else "normal",
            sentiment=sentiment,
            confidence=0.95,
            broker_name=demo.get("broker"),
            lane_from=demo.get("lane_from"),
            lane_to=demo.get("lane_to"),
            rate_usd=demo.get("rate"),
            evidence=(msg.body_text or "")[:160] or None,
        )

    subject = msg.subject or ""
    body = msg.body_text or ""
    labels_norm = {str(ln).lower() for ln in (msg.labels or [])}

    # OOO / autoresponder — never a complaint, never a human reply.
    if _is_autoreply(msg, subject=subject, labels=labels_norm):
        return TriageOutcome(
            intent="routine", urgency="low", sentiment=0.0, confidence=0.9,
            evidence=f"auto_reply: {subject[:120]}",
        )

    # Rate / load / booking confirmations and tracking-team notices.
    if _RATE_CONF_RE.search(subject) or _RATE_CONF_RE.search(body):
        return TriageOutcome(
            intent="booked", urgency="normal", sentiment=0.1, confidence=0.85,
            evidence=f"rate_confirmation: {subject[:120]}",
        )
    if _TRACKING_RE.search(body) or _TRACKING_RE.search(subject):
        return TriageOutcome(
            intent="routine", urgency="normal", sentiment=0.0, confidence=0.8,
            evidence="tracking_notice",
        )

    text = f"{subject}\n{body}"

    if _URGENT_RE.search(text):
        intent, conf, sent = "urgent_truck", 0.7, 0.0
    elif _COMPLAINT_RE.search(text):
        intent, conf, sent = "complaint", 0.75, -0.4
    elif _PAYMENT_RE.search(text):
        intent, conf, sent = "payment", 0.75, -0.2
    elif _DETENTION_RE.search(text):
        intent, conf, sent = "detention", 0.7, -0.2
    elif _PRAISE_RE.search(text):
        intent, conf, sent = "praise", 0.7, 0.5
    elif _BOOKED_RE.search(text):
        intent, conf, sent = "booked", 0.65, 0.3
    elif _LOAD_OFFER_RE.search(text):
        intent, conf, sent = "load_offer", 0.65, 0.1
    elif _RATE_REQ_RE.search(text):
        intent, conf, sent = "rate_request", 0.6, 0.0
    else:
        intent, conf, sent = "routine", 0.4, 0.0

    return TriageOutcome(
        intent=intent,
        urgency="urgent" if intent in URGENT_INTENTS else "normal",
        sentiment=sent,
        confidence=conf,
    )


async def _upsert_insight(session: AsyncSession, msg: RawMessage, o: TriageOutcome) -> None:
    existing = (
        await session.execute(
            select(MessageInsight).where(
                MessageInsight.mailbox == msg.mailbox,
                MessageInsight.message_id == msg.message_id,
                MessageInsight.model_version == MODEL_VERSION,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return
    session.add(
        MessageInsight(
            mailbox=msg.mailbox,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            from_email_normalized=(msg.from_addr or "").strip().lower(),
            intent=o.intent,
            urgency=o.urgency,
            sentiment=o.sentiment,
            confidence=o.confidence,
            rate_usd=o.rate_usd,
            lane_from=o.lane_from,
            lane_to=o.lane_to,
            equipment=o.equipment,
            broker_name=o.broker_name,
            evidence=o.evidence,
            model=MODEL_NAME,
            model_version=MODEL_VERSION,
        )
    )


async def _upsert_load_from_offer(session: AsyncSession, msg: RawMessage, o: TriageOutcome) -> None:
    """load_offer → ``loads`` row with ``source='inbox'``.

    Dedupe key: (source, source_ref)='inbox','<mailbox>:<message_id>'. A
    re-run of triage against the same message is a no-op.
    """
    if o.intent != "load_offer":
        return
    from app.prospecting.models import Load

    source_ref = f"{msg.mailbox}:{msg.message_id}"
    existing = (
        await session.execute(select(Load).where(Load.source == "inbox", Load.source_ref == source_ref))
    ).scalar_one_or_none()
    if existing is not None:
        return

    origin_city, origin_state = _split_city_state(o.lane_from)
    dest_city, dest_state = _split_city_state(o.lane_to)
    from app.prospecting.broker_matching import match_broker_lead

    broker_lead_id = await match_broker_lead(session, email=msg.from_addr or None, name=o.broker_name)
    session.add(
        Load(
            source="inbox",
            broker_lead_id=broker_lead_id,
            source_ref=source_ref,
            broker_name=o.broker_name or "",
            broker_email=msg.from_addr or None,
            origin_city=origin_city,
            origin_state=origin_state,
            dest_city=dest_city,
            dest_state=dest_state,
            pickup_date=None,
            equipment=o.equipment,
            rate_usd=o.rate_usd,
            posted_at=msg.sent_at,
            raw={"message_id": msg.message_id, "mailbox": msg.mailbox, "intent": o.intent},
        )
    )


def _split_city_state(lane: str | None) -> tuple[str | None, str | None]:
    if not lane:
        return None, None
    parts = [p.strip() for p in lane.split(",")]
    if len(parts) >= 2:
        return parts[0], parts[1][:8]
    return parts[0], None


# ---- signatures ------------------------------------------------------------

_PHONE_RE = re.compile(r"(?:\+?\d{1,2}[-. ]?)?\(?\d{3}\)?[-. ]?\d{3}[-. ]?\d{4}")
# Conservative title sniff — the two lines following a plausible name line in
# a signature block. Short-enough, title-case words with freight nouns. Noise
# is cheap here (bad title doesn't hurt dedupe — name_norm still matches),
# but we keep it narrow so we don't scrape body sentences.
_TITLE_LINE_RE = re.compile(r"^[A-Z][\w .&/'-]{2,60}$")


def _sniff_title(body_text: str | None, name: str | None) -> str | None:
    """Pull a plausible job title from the 2–3 lines after a name in the
    signature. Returns None when nothing freight-related is in reach.
    """
    if not body_text or not name:
        return None
    lines = [ln.strip() for ln in body_text.splitlines()]
    try:
        idx = next(
            i for i, ln in enumerate(lines) if name.split()[0] and name.lower() in ln.lower()
        )
    except StopIteration:
        return None
    for ln in lines[idx + 1 : idx + 4]:
        if not ln:
            continue
        if not _TITLE_LINE_RE.match(ln):
            continue
        # Nudge: prefer lines carrying a known freight keyword, but accept
        # any short titlecase label as a fallback — a non-freight title still
        # tags the contact correctly in the UI, and the segment filter will
        # just skip it.
        return ln
    return None


async def _maybe_extract_contact(session: AsyncSession, msg: RawMessage, broker_name: str | None) -> None:
    """If a lead exists by this sender's email-domain OR we sent *to* that lead's
    domain (inbound reply), add the signature contact.

    Deliberately conservative: we only *attach* contacts to existing leads.
    Creating new leads from an email signature is a separate flow (prospecting).
    This keeps the write small and the FK happy.
    """
    from app.prospecting.models import Lead, LeadContact

    if not msg.from_addr or "@" not in msg.from_addr:
        return
    local, _, domain = msg.from_addr.strip().lower().partition("@")
    if not domain:
        return
    lead = (await session.execute(select(Lead).where(Lead.domain == domain))).scalar_one_or_none()
    # Inbound-reply widening: when the sender's domain doesn't match a lead,
    # try the recipient's domain (we sent them something, they replied).
    if lead is None:
        to_addrs = msg.to_addrs or []
        for addr in to_addrs:
            if not addr or "@" not in addr:
                continue
            _, _, to_domain = addr.strip().lower().partition("@")
            if not to_domain:
                continue
            lead = (
                await session.execute(select(Lead).where(Lead.domain == to_domain))
            ).scalar_one_or_none()
            if lead is not None:
                break
    if lead is None:
        return
    # Avoid duplicate contact per (lead_id, email).
    dup = (
        await session.execute(
            select(LeadContact).where(LeadContact.lead_id == lead.id, LeadContact.email == msg.from_addr.lower())
        )
    ).scalar_one_or_none()
    if dup is not None:
        return
    phone = None
    phone_match = _PHONE_RE.search(msg.body_text or "")
    if phone_match:
        phone = phone_match.group(0)
    name = broker_name or local.title()
    title = _sniff_title(msg.body_text, name)
    session.add(
        LeadContact(
            lead_id=lead.id,
            name=name,
            email=msg.from_addr.lower(),
            email_norm=msg.from_addr.lower(),
            name_norm=name.strip().lower() if name else None,
            phone=phone,
            title=title,
            source="inbox-signature",
        )
    )


# ---- no-reply tracker -------------------------------------------------------


def _is_inbound(msg: RawMessage) -> bool:
    """A message is inbound when its from_addr isn't one of our mailboxes."""
    staff = (msg.mailbox or "").lower()
    from_lower = (msg.from_addr or "").lower()
    return from_lower != staff


async def _update_no_reply(session: AsyncSession, msg: RawMessage) -> None:
    """Inbound reply wipes the tracker row; outbound-last creates/refreshes it.

    An inbound OOO / autoresponder does NOT clear the tracker — the broker
    still owes us a human reply. Pre-fix an OOO looked like a reply and the
    "unanswered" chip undercounted (and the AI answer called the thread
    "answered already").
    """
    if _is_inbound(msg):
        subject = msg.subject or ""
        labels_norm = {str(ln).lower() for ln in (msg.labels or [])}
        if _is_autoreply(msg, subject=subject, labels=labels_norm):
            return
        await session.execute(
            delete(NoReplyTracker).where(
                NoReplyTracker.mailbox == msg.mailbox,
                NoReplyTracker.thread_id == msg.thread_id,
            )
        )
        return
    # Outbound: upsert the tracker row.
    to_addr = (msg.to_addrs or [""])[0] if msg.to_addrs else ""
    existing = (
        await session.execute(
            select(NoReplyTracker).where(
                NoReplyTracker.mailbox == msg.mailbox,
                NoReplyTracker.thread_id == msg.thread_id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            NoReplyTracker(
                mailbox=msg.mailbox,
                thread_id=msg.thread_id,
                to_email_normalized=(to_addr or "").strip().lower(),
                subject=msg.subject or "",
                we_sent_at=msg.sent_at,
            )
        )
    else:
        existing.we_sent_at = msg.sent_at
        existing.to_email_normalized = (to_addr or "").strip().lower()
        existing.subject = msg.subject or existing.subject
        existing.follow_up_suggested_at = None


async def triage_message(session: AsyncSession, msg: RawMessage) -> TriageOutcome:
    """Full on-ingest triage. One session, no commit (caller owns the UoW)."""
    outcome = _classify(msg)
    await _upsert_insight(session, msg, outcome)
    await _upsert_load_from_offer(session, msg, outcome)
    await _maybe_extract_contact(session, msg, outcome.broker_name)
    await _update_no_reply(session, msg)
    return outcome


async def retriage_all(session: AsyncSession) -> int:
    """Wipe ``message_insights`` + ``no_reply_tracker`` for the current
    tenant and re-run :func:`triage_message` over every ``mail_messages``
    row. Lets a classifier change land on existing local data without
    hand-SQL. Caller owns the UoW (one commit at the end).
    """
    from datetime import UTC, datetime

    from app.inbox.models import MailMessage
    from app.integrations.adapters.email.mailbox import RawMessage

    await session.execute(delete(MessageInsight))
    await session.execute(delete(NoReplyTracker))
    rows = list((await session.execute(select(MailMessage))).scalars().all())
    rows.sort(key=lambda r: r.sent_at or datetime.min.replace(tzinfo=UTC))
    for row in rows:
        rm = RawMessage(
            message_id=row.message_id,
            thread_id=row.thread_id,
            history_id=row.history_id or "",
            mailbox=row.mailbox,
            from_addr=row.from_addr or "",
            to_addrs=list(row.to_addrs or []),
            cc_addrs=list(row.cc_addrs or []),
            subject=row.subject or "",
            sent_at=row.sent_at,
            received_at=row.received_at or row.sent_at,
            in_reply_to=row.in_reply_to,
            references=list(row.references_hdr or []),
            body_text=row.body_text or "",
            body_html=row.body_html or "",
            labels=list(row.labels or []),
            raw=dict(row.raw or {}),
        )
        await triage_message(session, rm)
    return len(rows)


__all__ = ["MODEL_NAME", "MODEL_VERSION", "TriageOutcome", "retriage_all", "triage_message"]
