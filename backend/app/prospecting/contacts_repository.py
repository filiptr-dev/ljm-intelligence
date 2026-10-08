"""Contacts repository — the SQL seam for lead_contacts + provenance.

Follows the onion contract (projects/ljm-intelligence/plan/
2026-10-08-architecture-onion-solid.md): services depend on
``LeadContactRepo`` the Protocol; the concrete class below owns every
``select()`` / ``insert`` the feature needs.

The feature's dedupe guarantee lives in two places — a partial unique
index (``lead_contacts_lead_email_u`` / ``lead_contacts_lead_name_u``)
AND the ``upsert_from_source`` method here. The index is the strict
backstop; this method is the preferred, provenance-aware path.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prospecting.models import LeadContact, LeadContactProvenance

_WS_RE = re.compile(r"\s+")


def norm_email(email: str | None) -> str | None:
    if not email:
        return None
    s = email.strip().lower()
    return s or None


def norm_name(name: str | None) -> str | None:
    if not name:
        return None
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = _WS_RE.sub(" ", s).strip().lower()
    return s or None


@dataclass(frozen=True, slots=True)
class SourceObservation:
    """One sighting of a contact from a single source adapter."""

    source: str  # 'fmcsa-census' | 'gemini-search' | 'site-scrape' | 'inbox-signature'
    source_url: str | None
    name: str | None
    title: str | None
    email: str | None
    phone: str | None
    linkedin_url: str | None
    is_decision_maker: bool = False
    confidence: str = "medium"  # 'low' | 'medium' | 'high'
    evidence: dict[str, Any] | None = None


class LeadContactRepo(Protocol):
    async def by_lead(self, lead_id: str) -> list[LeadContact]: ...
    async def by_id(self, contact_id: int) -> LeadContact | None: ...
    async def list_segment(
        self,
        titles: tuple[str, ...],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[LeadContact], str | None]: ...
    async def find_dupe(
        self, lead_id: str, email_norm: str | None, name_norm: str | None
    ) -> LeadContact | None: ...
    async def upsert_from_source(
        self, lead_id: str, obs: SourceObservation
    ) -> tuple[LeadContact, bool]: ...
    async def provenance_count(self, contact_id: int) -> int: ...
    async def distinct_sources(self, contact_id: int) -> int: ...


class SqlLeadContactRepo:
    """Default (session-backed) impl of ``LeadContactRepo``."""

    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def by_lead(self, lead_id: str) -> list[LeadContact]:
        rows = (
            await self._s.execute(
                select(LeadContact)
                .where(LeadContact.lead_id == lead_id)
                .order_by(desc(LeadContact.discovered_at), desc(LeadContact.id))
            )
        ).scalars().all()
        return list(rows)

    async def by_id(self, contact_id: int) -> LeadContact | None:
        return (
            await self._s.execute(
                select(LeadContact).where(LeadContact.id == contact_id)
            )
        ).scalar_one_or_none()

    async def list_segment(
        self,
        titles: tuple[str, ...],
        cursor: str | None,
        limit: int,
    ) -> tuple[list[LeadContact], str | None]:
        """Keyset-paginated list of contacts whose title matches any of
        ``titles`` (case-insensitive, substring). Cursor shape: ``<iso>|<id>``.
        """
        # ILIKE OR chain — title list is small (~12 entries). GIN index is
        # a nice-to-have per plan §Migration; v1 uses the ILIKE scan.
        stmt = select(LeadContact).where(LeadContact.title.is_not(None))
        if titles:
            from sqlalchemy import or_

            terms = [LeadContact.title.ilike(f"%{t}%") for t in titles]
            stmt = stmt.where(or_(*terms))
        if cursor:
            iso, cid = cursor.split("|", 1)
            try:
                cursor_dt = datetime.fromisoformat(iso)
                cursor_id = int(cid)
            except (ValueError, TypeError):
                cursor_dt = None
                cursor_id = None
            if cursor_dt is not None and cursor_id is not None:
                stmt = stmt.where(
                    (LeadContact.discovered_at < cursor_dt)
                    | (
                        (LeadContact.discovered_at == cursor_dt)
                        & (LeadContact.id < cursor_id)
                    )
                )
        stmt = stmt.order_by(desc(LeadContact.discovered_at), desc(LeadContact.id)).limit(limit + 1)
        rows = list((await self._s.execute(stmt)).scalars().all())
        next_cursor: str | None = None
        if len(rows) > limit:
            last = rows[limit - 1]
            next_cursor = f"{last.discovered_at.isoformat()}|{last.id}"
            rows = rows[:limit]
        return rows, next_cursor

    async def find_dupe(
        self, lead_id: str, email_norm: str | None, name_norm: str | None
    ) -> LeadContact | None:
        if email_norm:
            row = (
                await self._s.execute(
                    select(LeadContact)
                    .where(LeadContact.lead_id == lead_id)
                    .where(LeadContact.email_norm == email_norm)
                )
            ).scalar_one_or_none()
            if row is not None:
                return row
        if name_norm:
            row = (
                await self._s.execute(
                    select(LeadContact)
                    .where(LeadContact.lead_id == lead_id)
                    .where(LeadContact.name_norm == name_norm)
                )
            ).scalar_one_or_none()
            return row
        return None

    async def upsert_from_source(
        self, lead_id: str, obs: SourceObservation
    ) -> tuple[LeadContact, bool]:
        """Merge-or-insert a contact. Always writes one provenance row.

        Trust uplift: distinct-source count ≥ 2 lifts confidence to ``high``.
        """
        email_norm = norm_email(obs.email)
        name_norm = norm_name(obs.name)
        existing = await self.find_dupe(lead_id, email_norm, name_norm)
        created = False
        if existing is None:
            existing = LeadContact(
                lead_id=lead_id,
                name=obs.name,
                title=obs.title,
                email=obs.email,
                phone=obs.phone,
                source=obs.source,
                source_url=obs.source_url,
                is_decision_maker=obs.is_decision_maker,
                linkedin_url=obs.linkedin_url,
                evidence=obs.evidence or {},
                confidence=obs.confidence,
                email_norm=email_norm,
                name_norm=name_norm,
            )
            self._s.add(existing)
            await self._s.flush()
            created = True
        else:
            # Merge — prefer non-null fields on existing, upgrade missing.
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
            if existing.name is None and obs.name:
                existing.name = obs.name
                existing.name_norm = name_norm
            # Merge evidence shallowly.
            merged_ev = dict(existing.evidence or {})
            merged_ev.update(obs.evidence or {})
            existing.evidence = merged_ev
            await self._s.flush()

        # Always append a provenance row (one per sighting).
        self._s.add(
            LeadContactProvenance(
                contact_id=existing.id,
                source=obs.source,
                source_url=obs.source_url or "",
                snippet=(obs.evidence or {}).get("snippet"),
                citations=(obs.evidence or {}).get("citations"),
            )
        )
        await self._s.flush()

        # Trust uplift.
        distinct = await self.distinct_sources(existing.id)
        if distinct >= 2:
            existing.confidence = "high"
            await self._s.flush()
        return existing, created

    async def provenance_count(self, contact_id: int) -> int:
        row = await self._s.execute(
            select(func.count())
            .select_from(LeadContactProvenance)
            .where(LeadContactProvenance.contact_id == contact_id)
        )
        return int(row.scalar() or 0)

    async def distinct_sources(self, contact_id: int) -> int:
        row = await self._s.execute(
            select(func.count(func.distinct(LeadContactProvenance.source))).where(
                LeadContactProvenance.contact_id == contact_id
            )
        )
        return int(row.scalar() or 0)
