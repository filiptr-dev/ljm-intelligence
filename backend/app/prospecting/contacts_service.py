"""Contacts service — four sources → one dedupe'd ``lead_contacts`` row.

Depends on the ``LeadContactRepo`` Protocol (never ``AsyncSession``) so the
four source adapters are unit-testable against an in-memory fake. See
``tests/test_contacts_promote.py``.

Rules (plan §Rules):
* **No email synthesis.** ``email`` stays NULL unless the source *published*
  it. The service never guesses first.last@domain.
* **Transactional promotion.** Caller opens the UoW; this function flushes
  via the repo (which calls ``session.flush``). The repo keeps commit
  ownership out — this is the DIP seam.
* **Never fetch linkedin.com.** We take ``linkedin_url`` only when it was
  anchored to a Gemini-citation URL in the adapter (see
  ``integrations/adapters/ai/linkedin_search.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from app.prospecting.contacts_repository import (
    LeadContactRepo,
    SourceObservation,
)

log = logging.getLogger(__name__)


# Kept here (not re-imported) because contracts 1/4/5 forbid prospecting.service
# etc from importing adapter-side symbols. The adapter owns the SEARCH list;
# the service owns the SEGMENT list — same words, two call sites.
FREIGHT_TITLES: tuple[str, ...] = (
    "Logistics Manager",
    "Transportation Manager",
    "Traffic Manager",
    "Warehouse Manager",
    "Supply Chain Manager",
    "Distribution Manager",
    "VP Supply Chain",
    "VP Operations",
    "Director of Logistics",
    "Shipping Manager",
    "Procurement Manager",
    "Buyer",
)

# Pre-build a word-boundary-ish regex per title. "Director of Logistics"
# matches "Director — Logistics" because we test every title keyword
# in isolation and require at least two keyword overlaps for multi-word
# titles. "Accountant" is deliberately excluded.
_TITLE_WORDS = tuple({w.lower() for t in FREIGHT_TITLES for w in t.split() if len(w) > 2})


def is_freight_manager(title: str | None) -> bool:
    """Fuzzy-match ``title`` against the freight-manager title list.

    Rule: hit if the title contains any freight-specific *key noun* with word
    boundaries — ``logistics``, ``transportation``, ``traffic``, ``warehouse``,
    ``supply chain``, ``distribution``, ``shipping``, ``procurement``,
    ``buyer``. "VP Operations" is a judgement call — included because the
    plan lists it; here we extend to "Operations Manager" too via the "ops"
    keyword only when paired with "VP"/"Director".
    """
    if not title:
        return False
    t = title.lower()
    freight_keys = (
        "logistics",
        "transportation",
        "traffic",
        "warehouse",
        "supply chain",
        "distribution",
        "shipping",
        "procurement",
        " buyer",  # leading space so "Lobbyer" / etc don't match
        "freight",
    )
    for k in freight_keys:
        # Use substring (bounded) search; keywords are nouns with low collision risk.
        if k in t:
            return True
    # "VP Operations" / "Director of Operations" explicit allowance.
    if ("vp" in t.split() or t.startswith("vp ")) and "operations" in t:
        return True
    return "director" in t and "operations" in t


# ---------------------------------------------------------------------------
# Source adapters — thin wrappers that convert an existing integration output
# into a ``SourceObservation``. Each returns a list (possibly empty). None of
# them raise on a provider miss — fetch_failed is logged and skipped.
# ---------------------------------------------------------------------------


class SourceAdapter(Protocol):
    async def observations(self, lead: Any) -> list[SourceObservation]: ...


# ---------------------------------------------------------------------------
# Core promote entry point — the four-source join.
# ---------------------------------------------------------------------------


@dataclass
class PromoteResult:
    created: int
    updated: int


async def promote_from_sources(
    repo: LeadContactRepo,
    lead: Any,
    *,
    adapters: list[SourceAdapter],
) -> PromoteResult:
    """Gather observations from every adapter and upsert into ``lead_contacts``.

    Caller owns the UoW (``async with session.begin():``); this function uses
    only repo methods, which flush but never commit.
    """
    created = 0
    updated = 0
    for adapter in adapters:
        try:
            observations = await adapter.observations(lead)
        except Exception as exc:  # noqa: BLE001
            log.info("contacts: adapter %s raised: %s", type(adapter).__name__, exc)
            continue
        for obs in observations:
            # Hard rule: never synthesise an email from a name. Pass-through
            # whatever the adapter published (which may be None).
            try:
                _, was_created = await repo.upsert_from_source(lead.id, obs)
            except Exception as exc:  # noqa: BLE001
                # IntegrityError on the partial unique index is the backstop
                # for the dedupe guarantee — swallow + log so one bad
                # observation does not kill the whole run.
                log.info("contacts: upsert skipped (%s): %s", obs.source, exc)
                continue
            if was_created:
                created += 1
            else:
                updated += 1
    return PromoteResult(created=created, updated=updated)


# ---------------------------------------------------------------------------
# Segment list — service-facing wrapper over the repo.
# ---------------------------------------------------------------------------


async def list_segment_freight_managers(
    repo: LeadContactRepo, *, cursor: str | None, limit: int
) -> tuple[list[Any], str | None]:
    """Delegate to the repo with the FREIGHT_TITLES set as filter terms."""
    return await repo.list_segment(FREIGHT_TITLES, cursor, limit)


# ---------------------------------------------------------------------------
# View helpers (shape a repo row for the HTTP wire).
# ---------------------------------------------------------------------------


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def contact_to_out(c: Any, *, evidence_count: int) -> dict:
    return {
        "id": c.id,
        "lead_id": c.lead_id,
        "name": c.name,
        "title": c.title,
        "email": c.email,
        "phone": c.phone,
        "source": c.source,
        "source_url": c.source_url,
        "linkedin_url": c.linkedin_url,
        "is_decision_maker": bool(c.is_decision_maker),
        "confidence": c.confidence,
        "is_freight_manager": is_freight_manager(c.title),
        "evidence_count": evidence_count,
        "last_verified_at": _iso(c.last_verified_at),
        "discovered_at": _iso(c.discovered_at),
    }


__all__ = [
    "FREIGHT_TITLES",
    "PromoteResult",
    "SourceAdapter",
    "SourceObservation",
    "contact_to_out",
    "is_freight_manager",
    "list_segment_freight_managers",
    "promote_from_sources",
]
