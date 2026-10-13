"""Deterministic lane / rate / order / pickup / equipment extractor.

Lives next to triage because it reads the same ``MailMessage`` shape and
targets the same ``MessageInsight`` row — but it is **additive**. It
never writes to ``intent/urgency/sentiment/rate_usd/lane_from/lane_to``:
those are triage's columns. It writes to the ``derived_*`` columns
(migration 0048) + populates/creates the matching ``Load`` row.

Two call sites:

* ``triage.triage_message`` — right after ``_upsert_insight``, so new
  mail stops producing "? → ?" loads on the /loads page.
* ``analysis.rate_backfill.backfill_subject_rates`` — one-shot over
  existing rows (73 inbox loads with null lane today).

Public API:

* :func:`extract_from_message` — pure function over ``(subject, body)``.
  Deterministic subject + body regex pass, quoted-reply text stripped.
  Returns ``None`` if nothing extracted.
* :func:`ai_extract_from_message` — optional AI fallback via the
  existing ``provider.get_for("inbox_analysis").generate_json`` surface.
  Called **only** when the deterministic pass misses the lane and the
  intent is one of ``{booked, load_offer, rate_request}``. Validates
  the JSON schema; never guesses.

Both honour a per-run cap so a tenant with 50k rows can't melt the AI
quota in one pass.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

EXTRACTOR_VERSION = "subject_body_v1"
AI_EXTRACTOR_VERSION = "ai_v1"

# Intents that are worth AI-extracting (triage already did the cheap pass).
LOAD_INTENTS: frozenset[str] = frozenset({"booked", "load_offer", "rate_request"})

# -- regexes --

# "Cleveland, OH to North Haven, CT" / "Chicago, IL -> Dallas, TX" /
# "Durham, NC - Kersey, PA". Allow lowercase city for the second word
# ("North Haven") and the trailing state is always 2 upper-case letters.
_LANE_RE = re.compile(
    r"([A-Z][A-Za-z. '-]+(?:\s+[A-Za-z. '-]+)*)\s*,\s*([A-Z]{2})"
    r"\s*(?:to|->|→|-|–|—|/|\|)+\s*"
    r"([A-Z][A-Za-z. '-]+(?:\s+[A-Za-z. '-]+)*)\s*,\s*([A-Z]{2})"
)

# "$1,700" / "$1700" / "rate: 1700" / "amount: 2,400.00". Must land on
# a plausible freight amount (100..1,000,000) — otherwise it's noise.
_RATE_DOLLAR_RE = re.compile(r"\$\s?(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{2})?")
_RATE_LABELED_RE = re.compile(
    r"(?:^|[\s(])(?:rate|amount|total|all[-\s]?in|flat)\s*[:=]?\s*\$?\s?"
    r"(\d{1,3}(?:,\d{3})*|\d+)(?:\.\d{2})?",
    re.IGNORECASE,
)

# "Load # 9737804" / "Order 2403768" / "PRO #31405-27609" / "Load Tender for 759605"
_ORDER_RE = re.compile(
    r"(?:load|order|pro|tender\s+for)\s*(?:no\.?|number)?\s*[#:]?\s*(\d[\d-]{3,})",
    re.IGNORECASE,
)

# Pickup date in body. Keep conservative — "pickup: 10/15" / "pickup date: 2026-10-15".
_PICKUP_RE = re.compile(
    r"(?:pick[-\s]?up|p/u|pu)\s*(?:date|on)?\s*[:=]?\s*"
    r"(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}(?:/\d{2,4})?)",
    re.IGNORECASE,
)

_EQUIPMENT_RE = re.compile(
    r"\b(dry\s*van|van|reefer|flatbed|flat\s*bed|step\s*deck|stepdeck|rgn|power\s*only)\b",
    re.IGNORECASE,
)

# Quoted-reply boundary — lines starting with ">" or an "On <date>, <name> wrote:" header
_QUOTED_LINE_RE = re.compile(r"^\s*>")
_QUOTED_HEADER_RE = re.compile(
    r"^\s*(?:on\s+.+\s+wrote:|from:\s+|-+\s*original message\s*-+)",
    re.IGNORECASE,
)


def _strip_quoted(body: str | None) -> str:
    """Return the leading non-quoted portion of ``body``.

    Stops at the first ``> …`` line or ``On <date>, <name> wrote:`` /
    ``From:`` / ``----- Original Message -----`` header. We don't want
    the inbound broker's quoted send to drive lane extraction on an
    outbound reply.
    """
    if not body:
        return ""
    out_lines: list[str] = []
    for line in body.splitlines():
        if _QUOTED_LINE_RE.match(line) or _QUOTED_HEADER_RE.match(line):
            break
        out_lines.append(line)
    return "\n".join(out_lines)


def _plausible_rate(raw: str) -> Decimal | None:
    try:
        v = Decimal(raw.replace(",", ""))
    except (ValueError, ArithmeticError):
        return None
    if v < Decimal(100) or v > Decimal(1_000_000):
        return None
    return v


def _parse_pickup(raw: str) -> date | None:
    """Parse a loose pickup date. We only emit the ``.date()`` so a naive
    datetime from ``strptime`` is intentional — ruff DTZ warnings don't
    apply to a date-only ingest value.
    """
    from datetime import UTC

    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m/%d"):
        try:
            dt = datetime.strptime(raw, fmt).replace(tzinfo=UTC)
            if fmt == "%m/%d":
                dt = dt.replace(year=datetime.now(UTC).year)
            return dt.date()
        except ValueError:
            continue
    return None


def _normalize_equipment(raw: str) -> str | None:
    s = raw.lower().replace("  ", " ").strip()
    s = s.replace("flat bed", "flatbed").replace("step deck", "stepdeck").replace("dry van", "van")
    return s or None


@dataclass(frozen=True, slots=True)
class ExtractedLoad:
    """What the extractor found. Any field may be None."""

    origin_city: str | None = None
    origin_state: str | None = None
    dest_city: str | None = None
    dest_state: str | None = None
    rate_usd: Decimal | None = None
    order_no: str | None = None
    pickup_date: date | None = None
    equipment: str | None = None
    source: str = EXTRACTOR_VERSION

    @property
    def has_lane(self) -> bool:
        return bool(self.origin_state and self.dest_state)

    @property
    def is_empty(self) -> bool:
        return (
            self.origin_state is None
            and self.dest_state is None
            and self.rate_usd is None
            and self.order_no is None
            and self.pickup_date is None
            and self.equipment is None
        )


def extract_from_message(subject: str | None, body: str | None) -> ExtractedLoad:
    """Run the deterministic pass over subject + non-quoted body.

    Pure function. Caller is responsible for scoping to the right
    intents (``LOAD_INTENTS``) so this doesn't try to pull a lane out
    of every ``routine`` ping. See the call sites in ``triage.py`` and
    ``rate_backfill.py``.
    """
    text_subject = subject or ""
    text_body = _strip_quoted(body)
    combined = f"{text_subject}\n{text_body}"

    origin_city = origin_state = dest_city = dest_state = None
    lm = _LANE_RE.search(combined)
    if lm is not None:
        origin_city, origin_state, dest_city, dest_state = (
            lm.group(1).strip(),
            lm.group(2).strip().upper(),
            lm.group(3).strip(),
            lm.group(4).strip().upper(),
        )

    # Rate: prefer labeled form, fall back to the first plausible $-amount.
    rate: Decimal | None = None
    for m in _RATE_LABELED_RE.finditer(combined):
        rate = _plausible_rate(m.group(1))
        if rate is not None:
            break
    if rate is None:
        for m in _RATE_DOLLAR_RE.finditer(combined):
            rate = _plausible_rate(m.group(1))
            if rate is not None:
                break

    order_no: str | None = None
    om = _ORDER_RE.search(combined)
    if om is not None:
        order_no = om.group(1)

    pickup: date | None = None
    pm = _PICKUP_RE.search(text_body)
    if pm is not None:
        pickup = _parse_pickup(pm.group(1))

    equipment: str | None = None
    em = _EQUIPMENT_RE.search(combined)
    if em is not None:
        equipment = _normalize_equipment(em.group(1))

    return ExtractedLoad(
        origin_city=origin_city,
        origin_state=origin_state,
        dest_city=dest_city,
        dest_state=dest_state,
        rate_usd=rate,
        order_no=order_no,
        pickup_date=pickup,
        equipment=equipment,
    )


# ---- AI fallback ----------------------------------------------------------

_AI_PROMPT = (
    "You are extracting freight load details from one broker email. "
    "Reply ONLY with a single JSON object. Any field you are not sure "
    "about must be null — never guess, never invent. State codes are "
    "2-letter US codes. Rate is a positive integer in USD without "
    "commas. Pickup is ISO 8601 (YYYY-MM-DD) or null. Equipment is one "
    'of: "van", "reefer", "flatbed", "stepdeck", "rgn", "power only", '
    "or null.\n\n"
    'Schema: {"origin_city": string|null, "origin_state": string|null, '
    '"dest_city": string|null, "dest_state": string|null, '
    '"rate_usd": integer|null, "order_no": string|null, '
    '"pickup_date": string|null, "equipment": string|null}\n\n'
)


def _valid_state(s: Any) -> str | None:
    if not isinstance(s, str):
        return None
    v = s.strip().upper()
    return v if len(v) == 2 and v.isalpha() else None


async def ai_extract_from_message(
    provider: Any, subject: str | None, body: str | None
) -> ExtractedLoad | None:
    """Call the AI fallback. Returns ``None`` on provider failure/null."""
    if provider is None or getattr(provider, "kind", None) == "null":
        return None
    text_subject = (subject or "")[:200]
    text_body = _strip_quoted(body)[:1500]
    prompt = _AI_PROMPT + f"Subject: {text_subject}\n\nBody:\n{text_body}\n"
    try:
        call = await provider.generate_json(prompt)
    except Exception as exc:  # noqa: BLE001
        log.warning("ai_extract provider error %s: %s", type(exc).__name__, exc)
        return None
    status = getattr(call, "status", None)
    if status != "ok":
        return None
    import json

    text = (getattr(call, "text", None) or "").strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        obj = json.loads(text)
    except ValueError:
        return None
    if not isinstance(obj, dict):
        return None

    rate_raw = obj.get("rate_usd")
    rate: Decimal | None = None
    if isinstance(rate_raw, (int, float)):
        try:
            v = Decimal(str(rate_raw))
            if Decimal(100) <= v <= Decimal(1_000_000):
                rate = v
        except (ValueError, ArithmeticError):
            rate = None

    pickup = None
    pr = obj.get("pickup_date")
    if isinstance(pr, str):
        pickup = _parse_pickup(pr)

    equipment = obj.get("equipment")
    if isinstance(equipment, str):
        equipment = _normalize_equipment(equipment)
    else:
        equipment = None

    return ExtractedLoad(
        origin_city=str(obj["origin_city"]).strip() if isinstance(obj.get("origin_city"), str) else None,
        origin_state=_valid_state(obj.get("origin_state")),
        dest_city=str(obj["dest_city"]).strip() if isinstance(obj.get("dest_city"), str) else None,
        dest_state=_valid_state(obj.get("dest_state")),
        rate_usd=rate,
        order_no=str(obj["order_no"]).strip() if isinstance(obj.get("order_no"), str) else None,
        pickup_date=pickup,
        equipment=equipment,
        source=AI_EXTRACTOR_VERSION,
    )


__all__ = [
    "AI_EXTRACTOR_VERSION",
    "EXTRACTOR_VERSION",
    "LOAD_INTENTS",
    "ExtractedLoad",
    "ai_extract_from_message",
    "extract_from_message",
]
