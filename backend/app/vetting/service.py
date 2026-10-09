"""Vetting service — composes the three reads and the pure rule table.

Takes Protocol ports so the router can hand in whatever repo + FMCSA
fetcher it wants; tests inject fakes without touching the network.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.integrations.adapters.enrichment.fmcsa import SNAPSHOT_URL_TMPL
from app.vetting import repository as repo
from app.vetting.domain import (
    BrokerSnapshot,
    PriorContact,
    Verdict,
    normalize_key,
    vet,
)


class LeadNotFoundError(Exception):
    pass


class FmcsaUnreachableError(Exception):
    """FMCSA live call failed AND no cache/lead fallback existed. True 'unknown'."""




class FmcsaPort(Protocol):
    async def fetch(self, dot: str) -> dict | None: ...


class _DefaultFmcsa:
    async def fetch(self, dot: str) -> dict | None:
        return await repo.live_fmcsa_fetch(dot)


@dataclass(frozen=True, slots=True)
class VetReport:
    key: str
    mc: str | None
    dot: str | None
    snapshot: BrokerSnapshot
    prior: PriorContact
    suppressed: bool
    verdict: Verdict
    evidence_url: str | None
    stale: bool
    lead_id: str | None


SNAPSHOT_TTL_DAYS = 30


async def _lookup_snapshot(
    s: AsyncSession,
    *,
    mc: str | None,
    dot: str | None,
    fmcsa: FmcsaPort,
    lead: object | None = None,
) -> tuple[BrokerSnapshot, str | None, bool, bool]:
    """Resolve snapshot via cache-then-live. Returns (snapshot, evidence_url, stale, upstream_failed).

    * cache hit, fresh → fresh snapshot, stale=False, upstream_failed=False.
    * cache miss → live fetch; on 200 cache and return fresh.
    * cache hit, STALE (>30d) → live fetch; on success refresh; on failure
      return cached row with stale=True, upstream_failed=True.
    * cache miss AND live fails AND we have an on-file Lead → synthesise a
      minimal snapshot from the lead row with stale=True, upstream_failed=True
      so the router can emit 503 instead of 404.
    * only truly unknown MC/DOT (no lead, no cache, live None) raises
      ``FmcsaUnreachableError`` which the router maps to 404.
    """
    # The cache is keyed on DOT. If the request is MC-only, try to resolve
    # DOT via the leads table; otherwise fall through to a live fetch which
    # itself only accepts DOT.
    if not dot and mc:
        from sqlalchemy import select

        from app.prospecting.models import Lead

        lead = (
            await s.execute(select(Lead).where(Lead.mc == mc).limit(1))
        ).scalar_one_or_none()
        if lead and lead.dot:
            dot = str(lead.dot)

    if not dot:
        # No DOT — but if we have a lead on file (matched by MC), use it as
        # a stale snapshot. Nothing to query, so no upstream call.
        if lead is not None:
            return (
                _snapshot_from_lead(lead, mc=mc, dot=dot),
                None,
                True,
                False,
            )
        raise FmcsaUnreachableError("no DOT to look up (MC not in local leads)")

    cached_payload, cached_at = await repo.read_fmcsa_cache(s, dot)
    now = datetime.now(UTC)
    fresh_cutoff = now - repo.SNAPSHOT_TTL
    evidence_url = SNAPSHOT_URL_TMPL.format(dot=dot)
    if cached_at is not None and cached_at.tzinfo is None:
        # SQLite returns naive timestamps; stamp UTC so the comparison is legal.
        cached_at = cached_at.replace(tzinfo=UTC)

    if cached_payload and cached_at and cached_at >= fresh_cutoff:
        return (
            repo.extract_snapshot_fields(cached_payload, mc=mc, dot=dot),
            evidence_url,
            False,
            False,
        )

    live = await fmcsa.fetch(dot)
    if live:
        await repo.write_fmcsa_cache(s, dot, live)
        return (
            repo.extract_snapshot_fields(live, mc=mc, dot=dot),
            evidence_url,
            False,
            False,
        )

    if cached_payload:
        return (
            repo.extract_snapshot_fields(cached_payload, mc=mc, dot=dot),
            evidence_url,
            True,
            True,
        )

    if lead is not None:
        return (
            _snapshot_from_lead(lead, mc=mc, dot=dot),
            evidence_url,
            True,
            True,
        )

    raise FmcsaUnreachableError("FMCSA snapshot unreachable and no cache row")


def _snapshot_from_lead(lead: object, *, mc: str | None, dot: str | None) -> BrokerSnapshot:
    """Synthesise a minimal snapshot from an on-file lead row.

    Enrichment only ingests active brokers, so authority_status='A' is a safe
    default; add_date is unknown (no 'new authority' flag will fire).
    """
    return BrokerSnapshot(
        mc=mc or getattr(lead, "mc", None),
        dot=str(dot) if dot else (str(getattr(lead, "dot", "")) or None),
        legal_name=getattr(lead, "name", None),
        dba_name=None,
        authority_status="A",
        add_date=None,
        oos_date=None,
        phone=getattr(lead, "phone", None),
        email=getattr(lead, "primary_email", None),
    )


async def vet_broker(
    sessionmaker: async_sessionmaker,
    raw_key: str,
    *,
    fmcsa: FmcsaPort | None = None,
) -> VetReport:
    """Public entry point. Normalises MC/DOT, composes reads, runs rules."""
    normalized = normalize_key(raw_key)
    if not normalized:
        raise LeadNotFoundError("invalid key")

    # Honour an explicit MC-/DOT- prefix first — users type it for a reason
    # (an 8-digit MC like "MC-50975720" otherwise flips to DOT and loses the
    # leads lookup entirely). Fall back to a length heuristic only when the
    # prefix is absent: 7+ digits trend DOT, shorter trends MC.
    raw_upper = raw_key.strip().upper()
    if raw_upper.startswith("MC"):
        mc: str | None = normalized
        dot: str | None = None
    elif raw_upper.startswith("DOT"):
        mc = None
        dot = normalized
    else:
        is_dot_hint = len(normalized) >= 7
        mc = None if is_dot_hint else normalized
        dot = normalized if is_dot_hint else None

    port = fmcsa or _DefaultFmcsa()
    async with sessionmaker() as s:
        # Lead lookup first — gives us the complementary id (MC↔DOT) and
        # lets prior-contact work even on MC-only input.
        lead = await repo.find_lead_by_mc_or_dot(s, mc, dot)
        if lead is not None:
            mc = mc or lead.mc
            dot = dot or (str(lead.dot) if lead.dot else None)

        snapshot, evidence_url, stale, upstream_failed = await _lookup_snapshot(
            s, mc=mc, dot=dot, fmcsa=port, lead=lead
        )

        prior = PriorContact()
        if lead is not None:
            prior = await repo.prior_contact(s, lead.id)

        suppressed = await repo.is_suppressed(s, snapshot.email)

    verdict = vet(snapshot, prior, suppressed)
    # ``upstream_failed`` is already surfaced to the client via ``stale=True``;
    # the UI shows a 'Cached snapshot — FMCSA was unreachable' line. We don't
    # fail the request when a fallback is available — only truly unknown
    # MC/DOT bubble up as ``FmcsaUnreachableError`` from ``_lookup_snapshot``.
    _ = upstream_failed
    return VetReport(
        key=raw_key,
        mc=mc,
        dot=dot,
        snapshot=snapshot,
        prior=prior,
        suppressed=suppressed,
        verdict=verdict,
        evidence_url=evidence_url,
        stale=stale,
        lead_id=lead.id if lead is not None else None,
    )
