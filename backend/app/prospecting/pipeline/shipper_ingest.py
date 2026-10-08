"""Slice 2b — persist FMCSA shippers + OSM candidates into `shipper_candidates`.

This is the thin, DB-facing layer that sits between the crawler and the
`shipper_candidates` table. All merge decisions come from
`app.pipeline.shipper_merge.match_and_merge` — the pure fn — so the choice
between "merge into existing" and "insert new row" is testable in isolation.

Contract per plan Slice 2b:

  * FMCSA leads with `kind='Shipper'` are projected into `shipper_candidates`
    with `sources=['FMCSA']`, `promoted_lead_id = lead.id` (already promoted),
    `fmcsa_dot`/`fmcsa_mc` set, and the source's raw row on `evidence.fmcsa`.
    Idempotent: re-running against the same lead hits rule (1) of the merge
    fn (`same fmcsa_dot` / `same fmcsa_mc`) and updates `last_seen_at`
    instead of inserting a second row.

  * OSM candidates flow through the same `match_and_merge` call. A matching
    FMCSA row picks up `osm_ref` + geometry + `"OSM"` on `sources` +
    `match_reason`; a non-matching row goes in as a new OSM-only entry.

  * Existing-row lookup is scoped by `state` (index-friendly), never a
    full-table scan.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.prospecting.models import ShipperCandidate
from app.prospecting.pipeline.shipper_merge import IncomingCandidate, match_and_merge

log = logging.getLogger(__name__)


def _new_candidate_id() -> str:
    """26-hex-char neutral id. Fits String(32); no source encoded."""
    return uuid.uuid4().hex[:26]


def _now_utc() -> datetime:
    return datetime.now(UTC)


async def _load_state_rows(session: AsyncSession, state: str) -> list[ShipperCandidate]:
    """Fetch existing candidates for one state.

    Index-backed (`shipper_candidates_state`), not a full-table scan.
    """
    res = await session.execute(select(ShipperCandidate).where(ShipperCandidate.state == state.upper()))
    return list(res.scalars().all())


def _merge_scalars(existing: ShipperCandidate, incoming: IncomingCandidate, extra: dict[str, Any]) -> None:
    """Apply the plan's prefer-non-null / prefer-FMCSA-for-authority / prefer-OSM-for-geometry rule."""
    src = incoming.source.upper()

    # Source list — dedupe, preserve order (FMCSA before OSM).
    sources = list(existing.sources or [])
    if src not in sources:
        # Keep FMCSA first when both present.
        if src == "FMCSA" and "OSM" in sources:
            sources = ["FMCSA", *[s for s in sources if s != "FMCSA"]]
        else:
            sources.append(src)
    existing.sources = sources

    if src == "FMCSA":
        # Authority — FMCSA wins when present.
        if incoming.fmcsa_dot:
            existing.fmcsa_dot = incoming.fmcsa_dot
            existing.dot = incoming.fmcsa_dot
        if incoming.fmcsa_mc:
            existing.fmcsa_mc = incoming.fmcsa_mc
            existing.mc = incoming.fmcsa_mc
        if incoming.domain and not existing.domain:
            existing.domain = incoming.domain
        if incoming.phone and not existing.phone:
            existing.phone = incoming.phone
        if extra.get("primary_email") and not existing.primary_email:
            existing.primary_email = extra["primary_email"]
        if extra.get("address") and not existing.address:
            existing.address = extra["address"]
        # Name — FMCSA legal name is preferred for outreach.
        if incoming.name and incoming.name.strip():
            existing.name = incoming.name.strip()
    elif src == "GEMINI":
        # Grounded discovery — fill nulls only, never overwrite authority.
        if incoming.domain and not existing.domain:
            existing.domain = incoming.domain
        if incoming.phone and not existing.phone:
            existing.phone = incoming.phone
        if extra.get("primary_email") and not existing.primary_email:
            existing.primary_email = extra["primary_email"]
        if not (existing.name or "").strip() and incoming.name:
            existing.name = incoming.name.strip()
    else:  # OSM
        if incoming.osm_ref:
            existing.osm_ref = incoming.osm_ref
        # Geometry — OSM wins.
        lat = extra.get("lat")
        lng = extra.get("lng")
        if lat is not None:
            existing.lat = lat
        if lng is not None:
            existing.lng = lng
        # Fill nulls, don't overwrite FMCSA authority.
        if incoming.phone and not existing.phone:
            existing.phone = incoming.phone
        if incoming.domain and not existing.domain:
            existing.domain = incoming.domain
        if extra.get("address") and not existing.address:
            existing.address = extra["address"]
        if extra.get("tags") is not None:
            existing.osm_tags = extra["tags"]
        # Name — keep existing (usually FMCSA legal name), unless it's blank.
        if not (existing.name or "").strip() and incoming.name:
            existing.name = incoming.name.strip()

    # City — fill if empty.
    if incoming.city and not existing.city:
        existing.city = incoming.city


def _merge_evidence(
    existing: ShipperCandidate,
    incoming: IncomingCandidate,
    extra: dict[str, Any],
    match_reason: str | None,
) -> None:
    """Never overwrite another source's evidence block."""
    ev = dict(existing.evidence or {})
    if incoming.source.upper() == "FMCSA" and "raw" in extra:
        ev["fmcsa"] = extra["raw"]
    if incoming.source.upper() == "OSM":
        ev["osm"] = {
            "ref": incoming.osm_ref,
            "tags": extra.get("tags"),
            "lat": extra.get("lat"),
            "lng": extra.get("lng"),
        }
    if match_reason:
        # High confidence for exact source-key hits; medium for corroborated cross-source matches.
        confidence = "high" if match_reason.startswith("same ") else "medium"
        ev["match"] = {"rule": match_reason, "confidence": confidence}
    existing.evidence = ev


async def _upsert_candidate(
    session: AsyncSession,
    incoming: IncomingCandidate,
    *,
    extra: dict[str, Any],
    promoted_lead_id: str | None,
    started: datetime,
) -> tuple[str, bool]:
    """Merge `incoming` into an existing row for its state, or insert a new row.

    Returns (id, inserted). `inserted=True` means we added a new row; False
    means we updated an existing one.
    """
    state = (incoming.state or "").upper()
    existing_rows = await _load_state_rows(session, state)
    decision = match_and_merge(existing_rows, incoming)

    if decision.action == "merge" and decision.target_id is not None:
        target = next((r for r in existing_rows if r.id == decision.target_id), None)
        if target is not None:
            _merge_scalars(target, incoming, extra)
            _merge_evidence(target, incoming, extra, decision.match_reason)
            if decision.match_reason:
                target.match_reason = decision.match_reason
            target.last_seen_at = started
            if promoted_lead_id and not target.promoted_lead_id:
                target.promoted_lead_id = promoted_lead_id
            return target.id, False

    # New row.
    new_id = _new_candidate_id()
    src = incoming.source.upper()
    row = ShipperCandidate(
        id=new_id,
        fmcsa_dot=incoming.fmcsa_dot,
        fmcsa_mc=incoming.fmcsa_mc,
        osm_ref=incoming.osm_ref,
        sources=[src],
        match_reason=None,
        name=(incoming.name or "").strip() or "(unknown)",
        state=state,
        city=incoming.city,
        address=extra.get("address"),
        lat=extra.get("lat"),
        lng=extra.get("lng"),
        mc=incoming.fmcsa_mc,
        dot=incoming.fmcsa_dot,
        domain=incoming.domain,
        phone=incoming.phone,
        primary_email=extra.get("primary_email"),
        osm_tags=extra.get("tags"),
        raw=extra.get("raw"),
        evidence={},
        promoted_lead_id=promoted_lead_id,
        first_seen_at=started,
        last_seen_at=started,
    )
    _merge_evidence(row, incoming, extra, None)
    session.add(row)
    return new_id, True


async def project_fmcsa_shippers_to_candidates(
    session: AsyncSession,
    leads: Iterable[Any],
    *,
    started: datetime | None = None,
) -> dict[str, int]:
    """Project every FMCSA lead with `kind='Shipper'` into `shipper_candidates`.

    Idempotent: a repeat sighting of the same MC/DOT hits rule (1) of
    `match_and_merge` and updates the existing row.

    Caller (`pipeline/run.py`) is responsible for the surrounding transaction /
    session lifecycle; this fn issues INSERTs + UPDATEs, no `commit`.
    """
    started = started or _now_utc()
    counts = {"projected_new": 0, "projected_updated": 0}
    for lead in leads:
        if getattr(lead, "kind", None) != "Shipper":
            continue
        state = (getattr(lead, "state", "") or "").upper()
        if not state:
            continue
        incoming = IncomingCandidate(
            source="FMCSA",
            name=getattr(lead, "name", "") or "",
            state=state,
            city=getattr(lead, "city", None),
            fmcsa_dot=getattr(lead, "dot", None),
            fmcsa_mc=getattr(lead, "mc", None),
            phone=getattr(lead, "phone", None),
            domain=getattr(lead, "domain", None),
        )
        extra = {
            "raw": getattr(lead, "raw", None),
            "primary_email": getattr(lead, "primary_email", None),
            "address": getattr(lead, "address", None),
        }
        _, inserted = await _upsert_candidate(
            session,
            incoming,
            extra=extra,
            promoted_lead_id=getattr(lead, "id", None),
            started=started,
        )
        # Flush so the next lead sees this row via the state scan (SQLite/pg alike).
        await session.flush()
        if inserted:
            counts["projected_new"] += 1
        else:
            counts["projected_updated"] += 1
    return counts


async def ingest_osm_incomings(
    session: AsyncSession,
    incomings: Iterable[IncomingCandidate],
    *,
    started: datetime | None = None,
) -> dict[str, int]:
    """Ingest a batch of OSM `IncomingCandidate`s (already mapped from Overpass elements).

    Each goes through `match_and_merge` — matching FMCSA rows pick up `osm_ref`
    + geometry + the "OSM" source label; non-matching go in as new OSM-only
    rows. See `osm_overpass.element_to_incoming` for the mapper.
    """
    started = started or _now_utc()
    counts = {"osm_new": 0, "osm_merged": 0}
    for inc in incomings:
        if inc.source.upper() != "OSM":
            # Defensive — this fn is OSM-only. FMCSA goes through the projector above.
            continue
        extra = dict(inc.extra)
        _, inserted = await _upsert_candidate(session, inc, extra=extra, promoted_lead_id=None, started=started)
        await session.flush()
        if inserted:
            counts["osm_new"] += 1
        else:
            counts["osm_merged"] += 1
    return counts
