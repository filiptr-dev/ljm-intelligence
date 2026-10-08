"""Adapters converting the four source modules into ``SourceObservation`` rows.

Each adapter is a tiny wrapper around an existing adapter in
``app/integrations/adapters/*`` or ``app/inbox/*``. Keeping them here (in
``prospecting``) honours the onion rule: FMCSA + Gemini + site scraper are
allowed from prospecting but not from the other modules (contract 4), so
the lift happens *inside* prospecting.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.prospecting.contacts_repository import SourceObservation
from app.prospecting.contacts_service import FREIGHT_TITLES

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 1. FMCSA snapshot officers.
# ---------------------------------------------------------------------------


@dataclass
class FmcsaSnapshotAdapter:
    """Pulls the per-DOT representative block via the snapshot endpoint.

    Uses the ``fmcsa_snapshot_cache`` table (30-day TTL) to avoid hammering
    the snapshot endpoint on repeat refreshes.
    """

    client: Any  # httpx.AsyncClient | None
    cache_fetch: Any  # async (dot) -> dict | None
    cache_write: Any  # async (dot, payload) -> None

    async def observations(self, lead: Any) -> list[SourceObservation]:
        dot = getattr(lead, "dot", None)
        if not dot:
            return []
        from app.integrations.adapters.enrichment.fmcsa import (
            SNAPSHOT_URL_TMPL,
            _extract_officers,
            fetch_fmcsa_snapshot,
        )

        cached = await self.cache_fetch(dot) if self.cache_fetch else None
        if cached:
            officers = _extract_officers(cached, SNAPSHOT_URL_TMPL.format(dot=dot))
        else:
            # Fetch fresh. The adapter stores the raw payload for TTL re-use.
            import httpx

            headers = {"User-Agent": "LJM-Intelligence-Bot/1.0"}
            owned = self.client is None
            client = self.client or httpx.AsyncClient(timeout=15.0)
            payload: dict | None = None
            try:
                resp = await client.get(SNAPSHOT_URL_TMPL.format(dot=dot), headers=headers)
                if resp.status_code == 200:
                    payload = resp.json()
            except Exception as exc:  # noqa: BLE001
                log.info("fmcsa_snapshot adapter: %s", exc)
                payload = None
            finally:
                if owned:
                    await client.aclose()
            officers = (
                _extract_officers(payload, SNAPSHOT_URL_TMPL.format(dot=dot))
                if payload
                else await fetch_fmcsa_snapshot(dot)
            )
            if payload and self.cache_write:
                try:
                    await self.cache_write(dot, payload)
                except Exception as exc:  # noqa: BLE001
                    log.info("fmcsa_snapshot cache_write failed: %s", exc)
        out: list[SourceObservation] = []
        for off in officers:
            out.append(
                SourceObservation(
                    source="fmcsa-census",
                    source_url=off.source_url,
                    name=off.name,
                    title=off.title,
                    email=off.email,  # may be None — never synthesised
                    phone=off.phone,
                    linkedin_url=None,
                    is_decision_maker=True,
                    confidence="high",
                    evidence={"dot": dot, "snippet": f"{off.name} — {off.title or ''}"},
                )
            )
        return out


# ---------------------------------------------------------------------------
# 2. Gemini search-grounded decision makers.
# ---------------------------------------------------------------------------


@dataclass
class GeminiDecisionMakersAdapter:
    provider: Any  # LLMProvider
    target_count: int = 5

    async def observations(self, lead: Any) -> list[SourceObservation]:
        if not self.provider:
            return []
        from app.integrations.adapters.ai.linkedin_search import (
            CompanyRef,
            find_decision_makers,
        )

        company = CompanyRef(
            name=lead.name,
            state=lead.state,
            kind=lead.kind,
            industry_hint=None,
            domain=getattr(lead, "domain", None),
        )
        hits = await find_decision_makers(company, provider=self.provider, target_count=self.target_count)
        if hits.status != "ok":
            return []
        out: list[SourceObservation] = []
        for p in hits.people:
            out.append(
                SourceObservation(
                    source="gemini-search",
                    source_url=p.evidence_url,
                    name=p.name,
                    title=p.title,
                    email=None,  # never fetch linkedin.com; email arrives from site/inbox
                    phone=None,
                    linkedin_url=p.linkedin_url,
                    is_decision_maker=True,
                    confidence="medium",
                    evidence={"citations": hits.citations},
                )
            )
        return out


# ---------------------------------------------------------------------------
# 3. Company site scraper.
# ---------------------------------------------------------------------------


@dataclass
class SiteScrapeAdapter:
    provider: Any
    fetcher: Any

    async def observations(self, lead: Any) -> list[SourceObservation]:
        domain = getattr(lead, "domain", None) or getattr(lead, "website_url", None)
        if not domain or not self.provider or not self.fetcher:
            return []
        from app.integrations.adapters.web.site_scraper import scrape_site

        titles_regex = "|".join(t.replace(" ", r"\s+") for t in FREIGHT_TITLES)
        hits = await scrape_site(domain, fetcher=self.fetcher, provider=self.provider, titles_regex=titles_regex)
        if hits.status != "ok" or hits.extraction is None:
            return []
        page_url = hits.page_provenance[0].final_url if hits.page_provenance else f"https://{domain}"
        out: list[SourceObservation] = []
        for person in (hits.extraction.people or []):
            name = (person.get("name") or "").strip() if isinstance(person, dict) else None
            title = (person.get("title") or "").strip() if isinstance(person, dict) else None
            email = (person.get("email") or "").strip() if isinstance(person, dict) else None
            phone = (person.get("phone") or "").strip() if isinstance(person, dict) else None
            if not name and not email:
                continue
            out.append(
                SourceObservation(
                    source="site-scrape",
                    source_url=page_url,
                    name=name or None,
                    title=title or None,
                    email=email or None,
                    phone=phone or None,
                    linkedin_url=None,
                    is_decision_maker=False,
                    confidence="medium",
                    evidence={"snippet": (title or "") + " " + (email or "")},
                )
            )
        # Fallback — standalone emails (no person block) still worth attaching.
        for mail in (hits.extraction.emails or []):
            if any(o.email == mail for o in out):
                continue
            out.append(
                SourceObservation(
                    source="site-scrape",
                    source_url=page_url,
                    name=None,
                    title=None,
                    email=mail,
                    phone=None,
                    linkedin_url=None,
                    is_decision_maker=False,
                    confidence="medium",
                    evidence={"snippet": mail},
                )
            )
        return out


# ---------------------------------------------------------------------------
# 4. Inbox signatures — attach-only, no new leads. See inbox/triage.py.
# ---------------------------------------------------------------------------


@dataclass
class InboxSignatureAdapter:
    """Pulls stored ``inbox-signature`` provenance into the generic flow.

    The inbox triage path already *writes* ``lead_contacts(source='inbox-signature')``
    inline on message ingest; this adapter is a no-op during refresh since
    those rows live on the same lead already. It stays as a seam so a future
    scan-the-mailbox refresh can plug into the same promote loop without
    changing callers.
    """

    async def observations(self, lead: Any) -> list[SourceObservation]:  # noqa: ARG002
        return []
