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


def _classify(msg: RawMessage) -> TriageOutcome:
    """Pure classifier.

    * Simulated corpus: ``raw["demo"]`` carries real labels.
    * Everything else: a conservative rules fallback over the subject/body.
      The AI path would replace this entire branch with one Gemini call.
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

    # Fallback rules — only triggered when ``raw["demo"]`` is absent (real Gmail
    # ingest + no AI). Favour SAFE over clever: unknowns bucket into "routine".
    text = f"{msg.subject}\n{msg.body_text}".lower()
    if any(k in text for k in ("urgent", "asap", "today", "live and")):
        intent = "urgent_truck"
    elif any(k in text for k in ("load offer", "load from", "target rate")):
        intent = "load_offer"
    elif any(k in text for k in ("invoice", "overdue", "unpaid")):
        intent = "payment"
    elif any(k in text for k in ("complaint", "not acceptable", "issue")):
        intent = "complaint"
    elif any(k in text for k in ("detention",)):
        intent = "detention"
    elif any(k in text for k in ("thanks", "great job", "nice work")):
        intent = "praise"
    elif any(k in text for k in ("confirmed", "booked", "you're covered")):
        intent = "booked"
    elif any(k in text for k in ("rate", "quote")):
        intent = "rate_request"
    else:
        intent = "routine"

    sentiment = 0.0
    if intent in ("complaint", "payment", "detention"):
        sentiment = -0.4
    elif intent in ("praise", "booked"):
        sentiment = 0.4

    return TriageOutcome(
        intent=intent,
        urgency="urgent" if intent in URGENT_INTENTS else "normal",
        sentiment=sentiment,
        confidence=0.55,
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
    session.add(
        Load(
            source="inbox",
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


async def _maybe_extract_contact(session: AsyncSession, msg: RawMessage, broker_name: str | None) -> None:
    """If a lead exists by this sender's email-domain, add the signature contact.

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
    session.add(
        LeadContact(
            lead_id=lead.id,
            name=broker_name or local.title(),
            email=msg.from_addr.lower(),
            phone=phone,
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
    """Inbound reply wipes the tracker row; outbound-last creates/refreshes it."""
    if _is_inbound(msg):
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


__all__ = ["MODEL_NAME", "MODEL_VERSION", "TriageOutcome", "triage_message"]
