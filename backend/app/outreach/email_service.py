"""Email service — draft (Gemini/Claude or template fallback) + send.

Thin business layer behind ``app/api/email.py``. The router is HTTP only;
this module owns:

* loading a lead by id (raises :class:`LeadNotFoundError` → 404 at the edge)
* resolving per-feature AI provider overrides from ``SettingsRow``
* calling the provider and recording usage, or falling back to the
  deterministic template when no provider is available
* persisting ``SentLog`` after a send

Pure "write the subject/body given a lead dict" helpers (``_stance``,
``_existing_broker_draft``, ``_fallback_draft``, ``_draft_prompt``) stay
exported as module-level functions so unit tests can keep patching them.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.analysis.ai_usage_service import hash_prompt, record
from app.identity.models import SettingsRow
from app.integrations.adapters.ai.provider import NullProvider, get_for
from app.integrations.adapters.email.sender import get_mail_sender
from app.outreach.brand import render_branded_email
from app.outreach.models import SentLog
from app.prospecting.models import Lead

log = logging.getLogger(__name__)


class LeadNotFoundError(Exception):
    """Raised when a lead id does not exist (router maps to 404)."""


Tone = Literal["professional", "friendly", "direct", "persuasive"]
Stance = Literal["positive", "neutral", "cooling"]

TONE_HINT: dict[Tone, str] = {
    "professional": "warm-professional, respectful, first-name-only greeting",
    "friendly": "friendly and human, still concise, no slang",
    "direct": "brief and direct, cut every filler word",
    "persuasive": "persuasive, lead with the value LJM brings, never oversell",
}

STANCE_HINT: dict[str, str] = {
    "positive": (
        "The relationship is warm (positive sentiment). Open with thanks for recent work, then make a "
        "direct availability pitch: we have a truck open this week, offer to hold it for them."
    ),
    "neutral": (
        "The relationship is neutral. Write a light intro / check-in: remind them who LJM is and ask "
        "what they have coming up, no hard sell."
    ),
    "cooling": (
        "The relationship is cooling (negative sentiment or it has gone quiet). Write a soft re-engage: "
        "acknowledge it has been a while, ask openly whether something on our side missed the mark "
        "(use top_reason if present), and end with a no-pressure offer to earn another shot."
    ),
}


@dataclass
class DraftResult:
    subject: str
    body: str
    body_html: str
    source: str  # "gemini" | "claude" | "fallback"
    tone: Tone
    lead_id: str | None
    stance: Stance | None


@dataclass
class SendResult:
    ok: bool
    mode: str  # "simulated" | "real"
    message_id: str | None
    thread_id: str | None
    reason: str | None = None


# ---------- pure helpers (kept module-level so tests can patch) -------------


def _num(v: object) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _first_name(full: str | None) -> str:
    return full.split()[0].title() if full else "there"


def _stance(lead: dict) -> Stance | None:
    """Derive the angle of the email from the lead's relationship signals.

    An explicit ``stance`` wins. Otherwise: negative sentiment, a big health drop or a long
    silence means cooling; clearly positive sentiment means positive; anything else with
    relationship data is neutral. No relationship data at all -> None (cold-lead template).
    """
    explicit = lead.get("stance")
    if explicit in ("positive", "neutral", "cooling"):
        return explicit  # type: ignore[return-value]
    sentiment = _num(lead.get("sentiment"))
    health_delta = _num(lead.get("health_delta"))
    days = _num(lead.get("days_since_last"))
    if sentiment is None and health_delta is None and days is None:
        return None
    if (
        (sentiment is not None and sentiment < -0.1)
        or (health_delta is not None and health_delta <= -10)
        or (days is not None and days >= 60)
    ):
        return "cooling"
    if sentiment is not None and sentiment > 0.2:
        return "positive"
    return "neutral"


def _existing_broker_draft(lead: dict, tone: Tone, stance: Stance) -> tuple[str, str]:
    """Template for a broker we already work with; the angle follows the stance."""
    name = lead.get("name") or "your team"
    first = _first_name(lead.get("contact_name") or lead.get("primary_contact"))
    lane = lead.get("top_lane")
    booked = int(_num(lead.get("booked")) or 0)
    days = int(_num(lead.get("days_since_last")) or 0)
    reason = lead.get("top_reason")
    opener = {
        "professional": "I hope you are doing well.",
        "friendly": "Hope your week is going well!",
        "direct": "",
        "persuasive": "",
    }[tone]
    paras = [f"Hi {first},"]
    if opener:
        paras.append(opener)

    if stance == "positive":
        subject = f"Truck open this week{f' on {lane}' if lane else ''}"
        paras.append(
            (
                f"Thanks again for the {booked} loads you have trusted us with. "
                if booked
                else "Thanks again for the recent work. "
            )
            + "It has been a pleasure running freight for "
            + f"{name}."
        )
        paras.append(
            f"We have a dry van opening up this week{f' that fits your {lane} lane' if lane else ''}. "
            + (
                "Same driver standards, live tracking, and a quote back within 15 minutes."
                if tone != "direct"
                else "Quote in 15 minutes."
            )
        )
        paras.append("Want me to hold it for you?")
    elif stance == "cooling":
        subject = f"Anything we can do better, {first}?" if first != "there" else f"Checking in from LJM, {name}"
        paras.append(
            f"It has been about {days} days since we last worked together, and I wanted to reach out personally."
            if days >= 14
            else "I wanted to reach out personally."
        )
        paras.append(
            "If something on our side missed the mark"
            + (f" (I know {str(reason).lower()} came up before)" if reason else "")
            + ", I would genuinely like to hear it so we can fix it."
        )
        paras.append("No pressure at all. If you have a load coming up, we would love another shot at earning it.")
    else:
        subject = f"Checking in from LJM International{f' about {lane}' if lane else ''}"
        paras.append(
            "Just a quick check-in from LJM International. We are still running dry vans across the eastern US"
            + (f", including {lane}" if lane else "")
            + "."
        )
        paras.append("What do you have coming up in the next couple of weeks? Happy to send a quick quote on anything.")

    paras.append("Best regards,\nNick Rivera\nLJM International")
    return subject, "\n\n".join(paras)


def _fallback_draft(lead: dict, tone: Tone) -> tuple[str, str]:
    stance = _stance(lead)
    if stance is not None:
        return _existing_broker_draft(lead, tone, stance)
    kind = lead.get("kind") or "Broker"
    name = lead.get("name") or "your team"
    first = _first_name(lead.get("contact_name") or lead.get("primary_contact"))
    where = ", ".join(x for x in (lead.get("city"), lead.get("state")) if x)
    subject = f"Direct dry-van capacity for {name}" if kind == "Shipper" else f"Dry-van capacity for {name}"
    opener = {
        "professional": "I hope you are doing well.",
        "friendly": "Hope you are having a good week!",
        "direct": "",
        "persuasive": "Quick note that could save you a few dollars a load.",
    }[tone]
    paras = [f"Hi {first},"]
    if opener:
        paras.append(opener)
    paras.append(
        f"I am reaching out from LJM International, a dry-van carrier in Lincoln Park, NJ, "
        f"running the eastern US. {name}"
        + (f" in {where}" if where else "")
        + " looks like a strong fit for our lanes."
    )
    paras.append(
        "Working with us directly means our own drivers, live tracking on every load, and quotes back within 15 minutes."
        if kind == "Shipper"
        else "We have consistent capacity across the northeast and southeast and quote within 15 minutes."
    )
    paras.append("Would you have a load this week we could cover as a first run?")
    paras.append("Best regards,\nNick Rivera\nLJM International")
    return subject, "\n\n".join(paras)


def _draft_prompt(lead: dict, tone: Tone, instructions: str | None) -> str:
    stance = _stance(lead)
    angle = f"ANGLE: {STANCE_HINT[stance]}\n" if stance else ""
    return (
        "You write outreach email drafts for LJM International, a US long-haul dry-van freight "
        "brokerage in Lincoln Park, NJ, serving the eastern US. Sender is Nick Rivera.\n\n"
        f"TONE: {TONE_HINT[tone]}\n"
        f"{angle}"
        f"EXTRA INSTRUCTIONS (optional): {instructions or 'none'}\n\n"
        "Rules:\n"
        "- Address them by first name if a contact name is known; otherwise a warm neutral opener.\n"
        "- 3-6 short paragraphs, plain text (no markdown, no HTML, no signature block).\n"
        "- Never claim facts you cannot verify from the LEAD JSON.\n"
        "- Do NOT add the LJM footer/address/phone; the app template handles that.\n"
        '- Return STRICT JSON: {"subject":"...","body":"..."} and nothing else.\n\n'
        f"LEAD JSON:\n{json.dumps(lead, default=str)}\n"
    )


async def _load_lead(session: AsyncSession, lead_id: str) -> dict:
    res = await session.execute(select(Lead).where(Lead.id == lead_id))
    lead = res.scalar_one_or_none()
    if not lead:
        raise LeadNotFoundError(lead_id)
    return {
        "id": lead.id,
        "name": lead.name,
        "kind": lead.kind,
        "city": lead.city,
        "state": lead.state,
        "mc": lead.mc,
        "dot": lead.dot,
        "domain": lead.domain,
        "primary_email": lead.primary_email,
        "phone": lead.phone,
        "current_score": lead.current_score,
        "evidence": lead.evidence or {},
    }


async def _load_ai_features(session: AsyncSession) -> dict | None:
    try:
        row = (
            await session.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        return (row.ai_features if row else None) or None
    except Exception as exc:  # noqa: BLE001
        log.info("email/draft: settings row lookup skipped: %s", exc)
        return None


async def draft(
    sessionmaker: Any,
    settings: Any,
    *,
    lead_id: str | None,
    lead_payload: dict | None,
    tone: Tone,
    instructions: str | None,
) -> DraftResult:
    """Compose a draft. ``lead_id`` or ``lead_payload`` must be set; router
    validates. Raises :class:`LeadNotFoundError` if ``lead_id`` is unknown."""
    if lead_id:
        async with sessionmaker() as s:
            lead = await _load_lead(s, lead_id)
    elif lead_payload:
        lead = dict(lead_payload)
    else:
        # Defensive — the router guards against this; keep a clear error for
        # any future direct caller.
        raise ValueError("provide lead_id or lead_payload")

    ai_features: dict | None = None
    try:
        async with sessionmaker() as s:
            ai_features = await _load_ai_features(s)
    except Exception as exc:  # noqa: BLE001
        log.info("email/draft: settings row lookup skipped: %s", exc)

    source: str = "fallback"
    provider = get_for("email_drafts", settings=settings, ai_features=ai_features)
    if isinstance(provider, NullProvider):
        subject, body = _fallback_draft(lead, tone)
    else:
        prompt = _draft_prompt(lead, tone, instructions)
        call = await provider.generate_json(prompt)
        try:
            async with sessionmaker() as s:
                await record(s, call, feature="email_drafts", input_hash=hash_prompt(prompt))
        except Exception as exc:  # noqa: BLE001
            log.warning("email/draft: ai_usage.record failed: %s", exc)
        parsed = call.parsed if isinstance(call.parsed, dict) else None
        if call.status == "ok" and parsed:
            subject = str(parsed.get("subject", "")).strip() or "Trucking capacity"
            body = str(parsed.get("body", "")).strip()
            if body:
                source = provider.kind if provider.kind in ("gemini", "claude") else "gemini"
            else:
                log.warning("email/draft: empty body, falling back")
                subject, body = _fallback_draft(lead, tone)
        else:
            log.warning(
                "email/draft: provider status=%s err=%s, falling back", call.status, call.error
            )
            subject, body = _fallback_draft(lead, tone)

    body_html = render_branded_email(
        subject=subject, body=body, frontend_origin=settings.frontend_origin
    )
    return DraftResult(
        subject=subject,
        body=body,
        body_html=body_html,
        source=source,
        tone=tone,
        lead_id=lead_id or lead.get("id"),
        stance=_stance(lead),
    )


async def send(
    sessionmaker: Any,
    settings: Any,
    *,
    to: str,
    subject: str,
    body: str,
    body_html: str | None,
    lead_id: str | None,
    in_reply_to: str | None,
    references: list[str] | None,
    thread_id: str | None,
) -> SendResult:
    """Resolve mode (DB override wins), send, persist a ``SentLog`` row."""
    # DB override wins over env for mode so /settings flips take effect live.
    async with sessionmaker() as s:
        row = (
            await s.execute(select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        mode_override = row.mail_sender_override if row else None
    sender = get_mail_sender(settings, mode_override=mode_override)
    result = await sender.send(
        to=to,
        subject=subject,
        body=body,
        body_html=body_html,
        in_reply_to=in_reply_to,
        references=references,
        thread_id=thread_id,
        from_addr=settings.outreach_from_email,
    )
    async with sessionmaker() as s:
        s.add(
            SentLog(
                lead_id=lead_id or "SYSTEM",
                mode=result.mode,
                to_email=to,
                subject=subject,
                body=body,
                provider_message_id=result.message_id,
                thread_id=result.thread_id,
                in_reply_to=in_reply_to,
            )
        )
        try:
            await s.commit()
        except Exception as exc:  # noqa: BLE001
            await s.rollback()
            log.warning("email/send: sent_log insert skipped: %s", exc)
    return SendResult(
        ok=result.error is None,
        mode=result.mode,
        message_id=result.message_id,
        thread_id=result.thread_id,
        reason=result.error,
    )
