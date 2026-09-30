"""Deterministic Shipper-Finder ranker for the operator's direct-shipper tool.

Given `candidates` (rows in `shipper_candidates`), prior `outcomes` (to suppress
already-`not_interested` promoted leads), and open capacity `posts`, return a
list of `ShipperRow`s ordered by score desc, then id asc (stable tiebreak → same
input, same list).

Mirrored on `pipeline/call_rank.py` in shape + spirit: pure function,
dependency-free, no Gemini, no DB, table-testable.

Signals (explicit weights, easy to tune):
  - FMCSA authority (source=FMCSA, MC or DOT present) → +40  ("Registered shipper — MC 123456")
  - Matches an open capacity post (origin state / dest)  → +25  ("Fits your NJ→PA truck")
  - Has phone                                             → +15
  - Has primary_email                                     → +10
  - OSM named DC or warehouse tag with a name             → +10  ("Named DC — <tag>")
  - Already promoted to `leads`                           → +5

Drop rules (applied before scoring):
  - `state` not in `IN_REGION_STATES` — defence in depth; the crawler already enforces
    this at ingest, but the ranker never trusts upstream (a row that leaks in must not
    surface to the operator).
  - No `name` — a nameless row has nothing to show; the OSM mapper is supposed to have
    synthesised a label ("Unnamed warehouse near <city>") for the two "obvious shipper"
    tags before we get here (see Slice 2).
  - Promoted lead is ever `not_interested` in `call_outcomes` — we honour the same
    permanent-suppression rule the call list uses; a shipper the operator has
    rejected must not resurface in the finder either.

Filters (applied after scoring so filter narrows are consistent regardless of order):
  - `state`         — exact 2-letter code match (case-insensitive)
  - `source`        — 'FMCSA' | 'OSM'
  - `min_score`     — score >= min_score
  - `promoted_only` — True → only promoted; False → only un-promoted; None → both
  - `q`             — case-insensitive substring on `name`
  - `limit`         — hard-capped at 200

Deterministic sort: (-score, id asc). Two calls with unchanged data → same list.

Note on `carship`: the plan says "FMCSA authority (carship='S', MC or DOT present)".
In this repo we don't currently persist a `carship` column on `shipper_candidates` —
the FMCSA projection puts source='FMCSA' *only* for rows where the crawler already
classified the entity as a Shipper (`Lead.kind='Shipper'`), so `source=='FMCSA'` +
(mc or dot) is the equivalent, honest signal. If we ever mirror `carship` verbatim,
this check tightens; until then, it never fabricates authority.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.models import CallOutcome, CapacityPost, ShipperCandidate
from app.region import IN_REGION_STATES

# Hard cap on server-side page size — Slice 3 will read this in the cursor pagination path
# so the ranker and the route never disagree on what "one page" means.
SHIPPER_FINDER_LIMIT_CAP: int = 200


@dataclass(frozen=True, slots=True)
class ShipperRow:
    id: str
    name: str
    state: str
    city: str | None
    address: str | None
    lat: float | None
    lng: float | None
    sources: tuple[str, ...]
    mc: str | None
    dot: str | None
    domain: str | None
    phone: str | None
    primary_email: str | None
    score: int
    reasons: tuple[str, ...] = field(default_factory=tuple)
    promoted_lead_id: str | None = None


@dataclass(frozen=True, slots=True)
class ShipperFilters:
    state: str | None = None
    source: str | None = None  # 'FMCSA' | 'OSM'
    min_score: int | None = None
    promoted_only: bool | None = None  # None → all; True → only promoted; False → only un-promoted
    q: str | None = None  # case-insensitive name substring
    limit: int = 50


# ---- helpers ---------------------------------------------------------------


def _leads_ever_not_interested(outcomes: Iterable[CallOutcome]) -> set[str]:
    by_lead: dict[str, list[CallOutcome]] = defaultdict(list)
    for o in outcomes:
        by_lead[o.lead_id].append(o)
    return {lid for lid, rows in by_lead.items() if any(r.outcome == "not_interested" for r in rows)}


def _post_matches_state(post: CapacityPost, state: str) -> str | None:
    """Return a lane chip if this open post fits a shipper in `state`, else None."""
    if getattr(post, "status", "open") != "open":
        return None
    st = (state or "").upper()
    os_ = (post.origin_state or "").upper()
    if post.kind == "truck":
        dests = [(d or "").upper() for d in (post.destinations or [])]
        if st and st == os_:
            head = dests[0] if dests else "?"
            return f"Fits your {os_}→{head} truck"
        if st and st in dests:
            return f"Fits your {os_}→{st} truck"
        return None
    if post.kind == "load":
        ds = (post.dest_state or "").upper()
        if st and (st == os_ or st == ds):
            return f"Fits your {os_}→{ds or '?'} load"
    return None


def _osm_named_shipping_tag(osm_tags: dict | None) -> str | None:
    """Return the short tag label if this OSM row is a clearly-shipping facility, else None.

    Only fires for `industrial=distribution_centre` and `building=warehouse` — the two
    tags that are unambiguously shipping facilities (see plan gate Q1). Generic
    `landuse=industrial` deliberately doesn't count here — the ingest layer drops
    unnamed ones and named ones don't earn a "Named DC" chip (too noisy — rail yards,
    quarries, power plants all wear this tag).
    """
    if not isinstance(osm_tags, dict):
        return None
    if osm_tags.get("industrial") == "distribution_centre":
        return "distribution_centre"
    if osm_tags.get("building") == "warehouse":
        return "warehouse"
    return None


def _authority_label(mc: str | None, dot: str | None) -> str:
    if mc:
        return f"MC {mc}"
    if dot:
        return f"DOT {dot}"
    return "authority"


# ---- main ------------------------------------------------------------------


def rank_shippers(
    candidates: Iterable[ShipperCandidate],
    posts: Iterable[CapacityPost],
    outcomes: Iterable[CallOutcome],
    today: date,  # reserved for future signals (staleness, recency); mirrors call_rank
    *,
    filters: ShipperFilters | None = None,
) -> list[ShipperRow]:
    """Score, filter, sort. Deterministic: same inputs → same outputs, always.

    `today` is threaded through so future signals (e.g. "recently first-seen" or a
    staleness drop) can land without changing the signature — mirrors call_rank's
    contract and keeps the route + tests already right for slice 3.
    """
    filters = filters or ShipperFilters()
    ni_leads = _leads_ever_not_interested(outcomes)
    open_posts = [p for p in posts if getattr(p, "status", "open") == "open"]

    rows: list[ShipperRow] = []
    for c in candidates:
        # Drops.
        state = (c.state or "").upper()
        if state not in IN_REGION_STATES:
            continue
        name = (c.name or "").strip()
        if not name:
            continue
        if c.promoted_lead_id and c.promoted_lead_id in ni_leads:
            continue

        # Score.
        score = 0
        reasons: list[str] = []
        source = (c.source or "").upper()

        # FMCSA authority.
        if source == "FMCSA" and (c.mc or c.dot):
            score += 40
            reasons.append(f"Registered shipper — {_authority_label(c.mc, c.dot)}")

        # Capacity-post fit.
        for post in open_posts:
            chip = _post_matches_state(post, state)
            if chip:
                score += 25
                reasons.append(chip)
                break  # one match is enough — don't stack the same signal

        # Contact fitness — phone.
        if (c.phone or "").strip():
            score += 15

        # Contact fitness — primary_email.
        if (c.primary_email or "").strip():
            score += 10

        # OSM named DC / warehouse.
        osm_label = _osm_named_shipping_tag(c.osm_tags)
        if osm_label is not None:
            score += 10
            reasons.append(f"Named DC — {osm_label}")

        # Already promoted — small nudge so known-good sits near the top.
        if c.promoted_lead_id:
            score += 5

        score = max(0, min(100, score))

        rows.append(
            ShipperRow(
                id=c.id,
                name=name,
                state=state,
                city=c.city,
                address=c.address,
                lat=c.lat,
                lng=c.lng,
                sources=(source,) if source else (),
                mc=c.mc,
                dot=c.dot,
                domain=c.domain,
                phone=(c.phone or None),
                primary_email=(c.primary_email or None),
                score=score,
                reasons=tuple(reasons),
                promoted_lead_id=c.promoted_lead_id,
            )
        )

    # Filters (applied after scoring so filter combinations narrow predictably).
    rows = _apply_filters(rows, filters)

    # Deterministic order: score desc, then id asc (stable tiebreak).
    rows.sort(key=lambda r: (-r.score, r.id))

    limit = max(0, min(SHIPPER_FINDER_LIMIT_CAP, int(filters.limit or 0)))
    return rows[:limit]


def _apply_filters(rows: list[ShipperRow], f: ShipperFilters) -> list[ShipperRow]:
    out = rows
    if f.state:
        st = f.state.upper()
        out = [r for r in out if r.state == st]
    if f.source:
        src = f.source.upper()
        out = [r for r in out if src in r.sources]
    if f.min_score is not None:
        m = int(f.min_score)
        out = [r for r in out if r.score >= m]
    if f.promoted_only is True:
        out = [r for r in out if r.promoted_lead_id]
    elif f.promoted_only is False:
        out = [r for r in out if not r.promoted_lead_id]
    if f.q:
        needle = f.q.strip().lower()
        if needle:
            out = [r for r in out if needle in r.name.lower()]
    return out


# Kept for parity with call_rank's UTC-aware sentinel idiom — if a future signal ever
# needs to compare a candidate's `first_seen_at` against a fallback, use this rather
# than bare `datetime.min` (see the 2026-09-29 call-list review: DTZ901 bug).
_DATETIME_MIN_UTC: datetime = datetime.min.replace(tzinfo=UTC)
