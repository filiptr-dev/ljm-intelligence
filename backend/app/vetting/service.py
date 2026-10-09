"""Vetting service — composes the three reads and the pure rule table.

Takes Protocol ports so the router can hand in whatever repo + FMCSA
fetcher it wants; tests inject fakes without touching the network.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
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
    # ISO-8601 timestamp of when the underlying data was captured.
    # For 'fmcsa_live' this is now; for 'fmcsa_cache' it's the cache row's
    # fetched_at; for 'lead_record' it's the lead's last_enriched_at /
    # last_seen_at / first_seen_at (whichever is most recent and non-null).
    snapshot_as_of: str | None = None


SNAPSHOT_TTL_DAYS = 30


async def _lookup_snapshot(
    s: AsyncSession,
    *,
    mc: str | None,
    dot: str | None,
    fmcsa: FmcsaPort,
    lead: object | None = None,
) -> tuple[BrokerSnapshot, str | None, bool, datetime | None]:
    """Resolve snapshot via cache-then-live. Returns (snapshot, evidence_url, stale, as_of).

    Every returned ``BrokerSnapshot`` carries a ``source`` tag so the UI and
    the verdict rules can tell FMCSA-confirmed data from a lead-row fallback.

    * cache hit, fresh → fresh snapshot, stale=False, source='fmcsa_cache'.
    * cache miss → live fetch; on 200 cache and return fresh (source='fmcsa_live').
    * cache hit, STALE (>30d) → live fetch; on success refresh; on failure
      return cached row with stale=True (source='fmcsa_cache').
    * cache miss AND live fails AND we have an on-file Lead → synthesise a
      minimal snapshot from the lead row with stale=True and source='lead_record'.
      The snapshot carries NO authority_status so the verdict degrades to
      caution via the ``authority_unverified`` red flag.
    * only truly unknown MC/DOT (no lead, no cache, live None) raises
      ``FmcsaUnreachableError`` which the router maps to 503.
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
                _lead_as_of(lead),
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
        snap = repo.extract_snapshot_fields(cached_payload, mc=mc, dot=dot)
        return (replace(snap, source="fmcsa_cache"), evidence_url, False, cached_at)

    live = await fmcsa.fetch(dot)
    if live:
        await repo.write_fmcsa_cache(s, dot, live)
        snap = repo.extract_snapshot_fields(live, mc=mc, dot=dot)
        return (replace(snap, source="fmcsa_live"), evidence_url, False, now)

    if cached_payload:
        snap = repo.extract_snapshot_fields(cached_payload, mc=mc, dot=dot)
        return (replace(snap, source="fmcsa_cache"), evidence_url, True, cached_at)

    if lead is not None:
        return (
            _snapshot_from_lead(lead, mc=mc, dot=dot),
            evidence_url,
            True,
            _lead_as_of(lead),
        )

    raise FmcsaUnreachableError("FMCSA snapshot unreachable and no cache row")


def _lead_as_of(lead: object) -> datetime | None:
    """Most-recent trustworthy timestamp on a lead row for the UI's 'as of' line."""
    for attr in ("last_enriched_at", "last_seen_at", "first_seen_at"):
        value = getattr(lead, attr, None)
        if value is not None:
            return value
    return None


def _snapshot_from_lead(lead: object, *, mc: str | None, dot: str | None) -> BrokerSnapshot:
    """Synthesise a minimal snapshot from an on-file lead row.

    The lead row proves the carrier exists in our universe — nothing more.
    FMCSA was NOT consulted for this response, so authority/OOS/add-date are
    left as None (unknown). ``vet()`` reads that None and raises the
    ``authority_unverified`` red flag, which routes the verdict to caution.
    Never fabricate authority_status='A' here — that produced a safe-looking
    verdict on data FMCSA never confirmed (prod audit 2026-10-09).
    """
    return BrokerSnapshot(
        mc=mc or getattr(lead, "mc", None),
        dot=str(dot) if dot else (str(getattr(lead, "dot", "")) or None),
        legal_name=getattr(lead, "name", None),
        dba_name=None,
        authority_status=None,
        add_date=None,
        oos_date=None,
        phone=getattr(lead, "phone", None),
        email=getattr(lead, "primary_email", None),
        source="lead_record",
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

        snapshot, evidence_url, stale, as_of = await _lookup_snapshot(
            s, mc=mc, dot=dot, fmcsa=port, lead=lead
        )

        prior = PriorContact()
        if lead is not None:
            prior = await repo.prior_contact(s, lead.id)

        suppressed = await repo.is_suppressed(s, snapshot.email)

    verdict = vet(snapshot, prior, suppressed)
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
        snapshot_as_of=as_of.isoformat() if as_of else None,
    )
