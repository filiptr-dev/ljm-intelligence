"""Email API: POST /email/draft.

Frontend calls this so the composer opens with subject + body already filled from
the real crawled lead. Gemini writes the draft; a deterministic template kicks in
when Gemini is unavailable so the composer is never left blank.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import Settings
from app.models import Lead, SettingsRow
from app.outreach.brand import render_branded_email
from app.services.ai_usage import hash_prompt, record
from app.sources.provider import NullProvider, get_for

log = logging.getLogger(__name__)

router = APIRouter(prefix="/email", tags=["email"])

Tone = Literal["professional", "friendly", "direct", "persuasive"]

TONE_HINT: dict[Tone, str] = {
    "professional": "warm-professional, respectful, first-name-only greeting",
    "friendly": "friendly and human, still concise, no slang",
    "direct": "brief and direct, cut every filler word",
    "persuasive": "persuasive, lead with the value LJM brings, never oversell",
}


class DraftIn(BaseModel):
    lead_id: str | None = None
    lead: dict | None = None
    tone: Tone = "professional"
    instructions: str | None = Field(default=None, max_length=1000)


Stance = Literal["positive", "neutral", "cooling"]

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


class DraftOut(BaseModel):
    subject: str
    body: str
    body_html: str
    # "gemini" | "claude" | "fallback". Named after the provider that wrote the
    # draft so the frontend can show the correct attribution ("Written by Claude…"
    # vs "Written by Gemini…" vs the local template fallback).
    source: Literal["gemini", "claude", "fallback"]
    tone: Tone
    lead_id: str | None = None
    # Only set when the lead carries relationship data (sentiment/health) or an explicit stance.
    stance: Stance | None = None


def _num(v: object) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


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


def _first_name(full: str | None) -> str:
    return full.split()[0].title() if full else "there"


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


async def _load_lead(request: Request, lead_id: str) -> dict:
    async with request.app.state.sessionmaker() as s:
        res = await s.execute(select(Lead).where(Lead.id == lead_id))
        lead = res.scalar_one_or_none()
    if not lead:
        raise HTTPException(404, "lead not found")
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


@router.post("/draft", response_model=DraftOut)
async def draft_email(payload: DraftIn, request: Request) -> DraftOut:
    settings: Settings = request.app.state.settings

    if payload.lead_id:
        lead = await _load_lead(request, payload.lead_id)
    elif payload.lead:
        lead = dict(payload.lead)
    else:
        raise HTTPException(400, "provide lead_id or lead")

    # Pull per-feature override from the settings row, if present. Resilient:
    # a missing table (test fixtures that skip migrations) or any read error
    # falls back to the code default — the draft route must never 500 on a
    # settings lookup.
    ai_features: dict | None = None
    try:
        async with request.app.state.sessionmaker() as s:
            row = (await s.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
            ai_features = (row.ai_features if row else None) or None
    except Exception as exc:  # noqa: BLE001
        log.info("email/draft: settings row lookup skipped: %s", exc)

    source: Literal["gemini", "claude", "fallback"] = "fallback"
    provider = get_for("email_drafts", settings=settings, ai_features=ai_features)
    subject: str
    body: str
    if isinstance(provider, NullProvider):
        subject, body = _fallback_draft(lead, payload.tone)
    else:
        prompt = _draft_prompt(lead, payload.tone, payload.instructions)
        call = await provider.generate_json(prompt)
        try:
            async with request.app.state.sessionmaker() as s:
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
                subject, body = _fallback_draft(lead, payload.tone)
        else:
            log.warning("email/draft: provider status=%s err=%s, falling back", call.status, call.error)
            subject, body = _fallback_draft(lead, payload.tone)

    body_html = render_branded_email(subject=subject, body=body, frontend_origin=settings.frontend_origin)
    return DraftOut(
        subject=subject,
        body=body,
        body_html=body_html,
        source=source,
        tone=payload.tone,
        lead_id=payload.lead_id or lead.get("id"),
        stance=_stance(lead),
    )
