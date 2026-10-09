"""Unit tests for contacts promote + dedupe + trust uplift.

Uses a tiny in-memory fake repo (DIP seam) so the four-source join is
testable with zero DB. The PG16 harness covers the partial-unique
backstop in ``test_contacts_segment.py``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from app.prospecting.contacts_repository import (
    SourceObservation,
    norm_email,
    norm_name,
)
from app.prospecting.contacts_service import (
    is_freight_manager,
    promote_from_sources,
)

# ---- fakes -----------------------------------------------------------------


@dataclass
class FakeContact:
    id: int
    lead_id: str
    name: str | None = None
    title: str | None = None
    email: str | None = None
    phone: str | None = None
    source: str | None = None
    source_url: str | None = None
    is_decision_maker: bool = False
    linkedin_url: str | None = None
    evidence: dict | None = None
    confidence: str | None = None
    email_norm: str | None = None
    name_norm: str | None = None


class FakeRepo:
    def __init__(self) -> None:
        self.contacts: list[FakeContact] = []
        self.provenance: list[tuple[int, str]] = []  # (contact_id, source)
        self._next = 1

    async def by_lead(self, lead_id: str):
        return [c for c in self.contacts if c.lead_id == lead_id]

    async def by_id(self, contact_id: int):
        return next((c for c in self.contacts if c.id == contact_id), None)

    async def find_dupe(self, lead_id, email_norm, name_norm):
        if email_norm:
            for c in self.contacts:
                if c.lead_id == lead_id and c.email_norm == email_norm:
                    return c
        if name_norm:
            for c in self.contacts:
                if c.lead_id == lead_id and c.name_norm == name_norm:
                    return c
        return None

    async def upsert_from_source(self, lead_id, obs: SourceObservation):
        email_norm = norm_email(obs.email)
        name_norm = norm_name(obs.name)
        existing = await self.find_dupe(lead_id, email_norm, name_norm)
        created = False
        if existing is None:
            existing = FakeContact(
                id=self._next, lead_id=lead_id, name=obs.name, title=obs.title,
                email=obs.email, phone=obs.phone, source=obs.source,
                source_url=obs.source_url, is_decision_maker=obs.is_decision_maker,
                linkedin_url=obs.linkedin_url, evidence=obs.evidence or {},
                confidence=obs.confidence, email_norm=email_norm, name_norm=name_norm,
            )
            self.contacts.append(existing)
            self._next += 1
            created = True
        else:
            if existing.email is None and obs.email:
                existing.email = obs.email
                existing.email_norm = email_norm
            if existing.phone is None and obs.phone:
                existing.phone = obs.phone
            if existing.title is None and obs.title:
                existing.title = obs.title
            if existing.linkedin_url is None and obs.linkedin_url:
                existing.linkedin_url = obs.linkedin_url
            if obs.is_decision_maker and not existing.is_decision_maker:
                existing.is_decision_maker = True
        self.provenance.append((existing.id, obs.source))
        # Trust uplift
        if len({s for cid, s in self.provenance if cid == existing.id}) >= 2:
            existing.confidence = "high"
        return existing, created

    async def provenance_count(self, contact_id):
        return sum(1 for cid, _ in self.provenance if cid == contact_id)

    async def distinct_sources(self, contact_id):
        return len({s for cid, s in self.provenance if cid == contact_id})

    async def list_segment(self, titles, cursor, limit):
        matches = [
            c for c in self.contacts
            if c.title and any(t.lower() in c.title.lower() for t in titles)
        ]
        return matches[:limit], None


@dataclass
class FakeLead:
    id: str
    name: str = "Acme"
    state: str = "NJ"
    kind: str = "Shipper"
    dot: str = "123"
    domain: str = "acme.com"


@dataclass
class StaticAdapter:
    obs: list[SourceObservation] = field(default_factory=list)

    async def observations(self, lead: Any):
        return self.obs


# ---- AC2 — four sources wired -----------------------------------------------


@pytest.mark.asyncio
async def test_four_sources_write_four_contacts_four_provenance():
    repo = FakeRepo()
    lead = FakeLead(id="MC-1")
    adapters = [
        StaticAdapter([SourceObservation("fmcsa-census", "https://x", "Alice", "Owner", None, "555-1", None, True, "high")]),
        StaticAdapter([SourceObservation("gemini-search", "https://g", "Bob", "Logistics Manager", None, None, "https://linkedin.com/in/bob", True, "medium")]),
        StaticAdapter([SourceObservation("site-scrape", "https://acme.com/contact", "Carol", "Shipping Manager", "carol@acme.com", None, None, False, "medium")]),
        StaticAdapter([SourceObservation("inbox-signature", None, "Dan", "Traffic Manager", "dan@acme.com", "555-2", None, False, "high")]),
    ]
    r = await promote_from_sources(repo, lead, adapters=adapters)
    assert r.created == 4
    assert len(repo.contacts) == 4
    assert len(repo.provenance) == 4
    sources = {o.source for o in repo.contacts}
    assert sources == {"fmcsa-census", "gemini-search", "site-scrape", "inbox-signature"}


# ---- AC3 — no email synthesis, cross-source upgrade -------------------------


@pytest.mark.asyncio
async def test_gemini_then_site_merges_and_promotes_confidence():
    repo = FakeRepo()
    lead = FakeLead(id="MC-2")
    # Gemini: name + title, no email (never synthesised).
    await promote_from_sources(repo, lead, adapters=[StaticAdapter([
        SourceObservation("gemini-search", "https://g", "Jane Smith", "Director of Logistics", None, None, "https://linkedin.com/in/jane", True, "medium"),
    ])])
    assert len(repo.contacts) == 1
    assert repo.contacts[0].email is None  # no synthesis
    # Site scrape: same name, now with a published email.
    await promote_from_sources(repo, lead, adapters=[StaticAdapter([
        SourceObservation("site-scrape", "https://acme.com/team", "Jane Smith", "Director of Logistics", "jane@acme.com", None, None, False, "medium"),
    ])])
    assert len(repo.contacts) == 1  # merged
    merged = repo.contacts[0]
    assert merged.email == "jane@acme.com"
    assert merged.confidence == "high"  # trust uplift


# ---- AC4 — dedupe idempotency ----------------------------------------------


@pytest.mark.asyncio
async def test_promote_twice_is_zero_new_contacts():
    repo = FakeRepo()
    lead = FakeLead(id="MC-3")
    obs = [SourceObservation("fmcsa-census", "https://x", "Alice", "Owner", "alice@acme.com", None, None, True, "high")]
    await promote_from_sources(repo, lead, adapters=[StaticAdapter(obs)])
    assert len(repo.contacts) == 1
    assert len(repo.provenance) == 1
    await promote_from_sources(repo, lead, adapters=[StaticAdapter(obs)])
    assert len(repo.contacts) == 1  # same row
    assert len(repo.provenance) == 2  # second sighting


# ---- AC5 — freight-manager classifier --------------------------------------


def test_is_freight_manager_matches_canonical_titles():
    assert is_freight_manager("Logistics Manager") is True
    assert is_freight_manager("Director of Logistics") is True
    assert is_freight_manager("VP Supply Chain") is True
    assert is_freight_manager("Shipping Coordinator") is True  # 'shipping' key
    assert is_freight_manager("Director — Logistics") is True
    assert is_freight_manager("Senior Buyer") is True


def test_is_freight_manager_excludes_other_titles():
    assert is_freight_manager("Accountant") is False
    assert is_freight_manager("Software Engineer") is False
    assert is_freight_manager(None) is False
    assert is_freight_manager("") is False
