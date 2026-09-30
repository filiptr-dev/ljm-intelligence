"""Fit score — deterministic 0..100 per lead / candidate + human-readable reasons.

Consumes signals harvested by the enrichment scraper (website text markers,
extractor's freight-relevant people list, phones/emails, industry hints, state)
and outputs a bounded integer score with a JSON-friendly reasons list.

Design
------
* **Pure function.** No DB, no network. Same inputs → same outputs. Unit-testable.
* **Weights come from settings.** `DEFAULT_WEIGHTS` is the baseline; the settings
  row's `fit_weights` JSONB can override any key. Missing keys fall back to the
  default; unknown keys are ignored. Ops can widen a signal without a deploy.
* **Loud low-data path.** When we have essentially nothing to score on, the
  function returns `(0, ["not enough data"])` rather than a made-up baseline.
  A "0 with a reason" is better than a "50 that isn't earned".

Signals (structured, extractable server-side):

  * `is_in_region` — bool. Lead state in `IN_REGION_STATES`.
  * `has_named_decision_maker_with_direct_contact` — bool. Any person contact
    with title matching freight-relevant regex AND email/phone that is NOT the
    generic `info@` / `hello@` / `contact@` bucket.
  * `has_only_generic_email` — bool. All emails are info@/hello@/contact@.
  * `has_warehouse_or_dc_mention` — bool. "warehouse", "distribution center",
    "distribution centre", "dc network", "fulfillment center" in site text.
  * `ships_nationwide` — bool. "ship nationwide", "nationwide shipping", "us-wide", "coast to coast".
  * `industry_freight_heavy` — bool. Industry keyword hit from the freight-heavy list.
  * `equipment_dry_van_or_reefer_or_flatbed` — bool. Text markers for our equipment.
  * `locations_count` — int. Number of "location" / "warehouse" / address blocks (approximate).
  * `has_phone` — bool.

`SiteSignals` is the shape enrichment produces per lead; `compute_fit` is what
callers hit.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from app.region import IN_REGION_STATES

# ---------- signals ---------------------------------------------------------


GENERIC_LOCAL_PARTS: frozenset[str] = frozenset(
    {"info", "hello", "contact", "office", "support", "sales", "admin", "hi", "team"}
)


@dataclass(frozen=True, slots=True)
class SiteSignals:
    state: str = ""
    has_named_decision_maker_with_direct_contact: bool = False
    has_only_generic_email: bool = False
    has_warehouse_or_dc_mention: bool = False
    ships_nationwide: bool = False
    industry_freight_heavy: bool = False
    equipment_dry_van_or_reefer_or_flatbed: bool = False
    locations_count: int = 0
    has_phone: bool = False
    # Small evidence blob — surfaces into `fit_reasons`/`fit_score_history.signals`.
    industry_keywords_hit: tuple[str, ...] = field(default_factory=tuple)
    equipment_keywords_hit: tuple[str, ...] = field(default_factory=tuple)


DEFAULT_WEIGHTS: dict[str, int] = {
    "in_region": 15,
    "named_dm_direct_contact": 25,
    "warehouse_or_dc": 15,
    "ships_nationwide": 10,
    "industry_freight_heavy": 10,
    "equipment_hint": 10,
    "locations_bonus_per_extra": 3,  # +N per location > 1, capped at 3
    "has_phone": 5,
    # Penalties (subtracted).
    "penalty_generic_email_only": -10,
}


FREIGHT_HEAVY_INDUSTRIES: tuple[str, ...] = (
    "retail",
    "food",
    "beverage",
    "manufacturing",
    "building materials",
    "paper",
    "packaging",
    "chemicals",
    "steel",
    "metals",
    "agriculture",
    "automotive",
)

EQUIPMENT_KEYWORDS: tuple[str, ...] = ("dry van", "reefer", "refrigerated", "flatbed", "ltl", "less than truckload")

_WAREHOUSE_RE = re.compile(
    r"\b(warehouse|distribution\s*cent(?:er|re)|dc\s+network|fulfillment\s*cent(?:er|re))\b",
    re.IGNORECASE,
)
_NATIONWIDE_RE = re.compile(
    r"\b(ship\w*\s+nationwide|nationwide\s+shipping|us[\s-]?wide|coast\s*to\s*coast)\b", re.IGNORECASE
)


def extract_signals_from_text(text: str) -> dict[str, Any]:
    """Cheap regex-based signals derived from cleaned website text.

    Returns dict of the SiteSignals fields the enrichment stage can layer on top
    of contact-derived signals. Deterministic; used both live and in tests.
    """
    lo = (text or "").lower()
    industry_hits = tuple(sorted({k for k in FREIGHT_HEAVY_INDUSTRIES if k in lo}))
    equipment_hits = tuple(sorted({k for k in EQUIPMENT_KEYWORDS if k in lo}))
    warehouse = bool(_WAREHOUSE_RE.search(text or ""))
    nationwide = bool(_NATIONWIDE_RE.search(text or ""))
    # Naive location-count proxy: warehouse/distribution mentions + address-like patterns.
    locations = 0
    if warehouse:
        locations += len(_WAREHOUSE_RE.findall(text or ""))
    return {
        "industry_keywords_hit": industry_hits,
        "equipment_keywords_hit": equipment_hits,
        "has_warehouse_or_dc_mention": warehouse,
        "ships_nationwide": nationwide,
        "industry_freight_heavy": bool(industry_hits),
        "equipment_dry_van_or_reefer_or_flatbed": bool(equipment_hits),
        "locations_count": min(20, locations),
    }


def is_generic_email(email: str | None) -> bool:
    if not email or "@" not in email:
        return False
    local = email.split("@", 1)[0].lower().strip()
    return local in GENERIC_LOCAL_PARTS


def build_signals(
    *,
    state: str | None,
    contacts: list[dict],
    text: str = "",
) -> SiteSignals:
    """Fold DB/extractor evidence + text signals into one `SiteSignals`.

    `contacts` is a list of dicts with keys `email`, `phone`, `title`, `is_dm`.
    """
    text_signals = extract_signals_from_text(text or "")

    emails = [c.get("email") for c in contacts if c.get("email")]
    phones = [c.get("phone") for c in contacts if c.get("phone")]
    has_phone = bool(phones)
    only_generic = bool(emails) and all(is_generic_email(e) for e in emails)
    named_dm_direct = any(
        c.get("is_dm") and (c.get("email") and not is_generic_email(c.get("email")) or c.get("phone")) for c in contacts
    )

    return SiteSignals(
        state=(state or "").upper(),
        has_named_decision_maker_with_direct_contact=named_dm_direct,
        has_only_generic_email=only_generic,
        has_warehouse_or_dc_mention=bool(text_signals["has_warehouse_or_dc_mention"]),
        ships_nationwide=bool(text_signals["ships_nationwide"]),
        industry_freight_heavy=bool(text_signals["industry_freight_heavy"]),
        equipment_dry_van_or_reefer_or_flatbed=bool(text_signals["equipment_dry_van_or_reefer_or_flatbed"]),
        locations_count=int(text_signals["locations_count"]),
        has_phone=has_phone,
        industry_keywords_hit=tuple(text_signals["industry_keywords_hit"]),
        equipment_keywords_hit=tuple(text_signals["equipment_keywords_hit"]),
    )


def _weight(weights: dict[str, int] | None, key: str) -> int:
    if not weights:
        return DEFAULT_WEIGHTS[key]
    v = weights.get(key)
    if v is None:
        return DEFAULT_WEIGHTS[key]
    try:
        return int(v)
    except (TypeError, ValueError):
        return DEFAULT_WEIGHTS[key]


def _low_data(signals: SiteSignals) -> bool:
    """True when we have essentially nothing to score on."""
    return (
        not signals.state
        and not signals.has_named_decision_maker_with_direct_contact
        and not signals.has_only_generic_email
        and not signals.has_phone
        and not signals.has_warehouse_or_dc_mention
        and not signals.ships_nationwide
        and not signals.industry_freight_heavy
        and not signals.equipment_dry_van_or_reefer_or_flatbed
        and signals.locations_count == 0
    )


def compute_fit(signals: SiteSignals, weights: dict[str, int] | None = None) -> tuple[int, list[str]]:
    """Return `(score 0..100, human-readable reasons)`.

    Deterministic. The score is clamped to `[0, 100]`; reasons are listed in the
    order signals fired so the UI reads like a sentence: "In-region · Named DM
    with direct email · Warehouse mentioned".
    """
    if _low_data(signals):
        return 0, ["not enough data"]

    reasons: list[str] = []
    score = 0

    if signals.state and signals.state in IN_REGION_STATES:
        score += _weight(weights, "in_region")
        reasons.append(f"In-region ({signals.state})")

    if signals.has_named_decision_maker_with_direct_contact:
        score += _weight(weights, "named_dm_direct_contact")
        reasons.append("Named decision-maker with direct email/phone")

    if signals.has_warehouse_or_dc_mention:
        score += _weight(weights, "warehouse_or_dc")
        reasons.append("Warehouse / distribution center mentioned")

    if signals.ships_nationwide:
        score += _weight(weights, "ships_nationwide")
        reasons.append("Ships nationwide")

    if signals.industry_freight_heavy:
        score += _weight(weights, "industry_freight_heavy")
        reasons.append("Freight-heavy industry (" + ", ".join(signals.industry_keywords_hit) + ")")

    if signals.equipment_dry_van_or_reefer_or_flatbed:
        score += _weight(weights, "equipment_hint")
        reasons.append("Equipment hint (" + ", ".join(signals.equipment_keywords_hit) + ")")

    if signals.locations_count > 1:
        bonus = min(3, signals.locations_count - 1) * _weight(weights, "locations_bonus_per_extra")
        if bonus > 0:
            score += bonus
            reasons.append(f"{signals.locations_count} locations noted")

    if signals.has_phone:
        score += _weight(weights, "has_phone")
        reasons.append("Phone on file")

    # Penalty — generic-only email pulls score down but never below 0.
    if signals.has_only_generic_email and not signals.has_named_decision_maker_with_direct_contact:
        penalty = _weight(weights, "penalty_generic_email_only")
        score += penalty
        reasons.append("Only generic email (info@ / hello@)")

    score = max(0, min(100, score))
    return score, reasons
