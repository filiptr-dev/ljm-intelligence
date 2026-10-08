"""Deterministic Shipper-Finder ranker for the operator's direct-shipper tool.

Given `candidates` (rows in `shipper_candidates`), prior `outcomes` (to suppress
already-`not_interested` promoted leads), and open capacity `posts`, return a
list of `ShipperRow`s ordered by score desc, then id asc (stable tiebreak → same
input, same list).

Mirrored on `pipeline/call_rank.py` in shape + spirit: pure function,
dependency-free, no Gemini, no DB, table-testable.

Signals (explicit weights, easy to tune):
  - FMCSA authority (fmcsa_dot or fmcsa_mc set)          → +40  ("Registered shipper — MC 123456")
  - Matches an open capacity post (origin state / dest)  → +25  ("Fits your NJ→PA truck")
  - Has phone                                             → +15
  - Has primary_email                                     → +10
  - OSM named DC or warehouse tag with a name             → +10  ("Named DC — <tag>")
  - Already promoted to `leads`                           → +5

Merged-row scoring (Slice 2a — see plan amendment): rows carry a `sources` JSONB
list, and both FMCSA + OSM signals stack on the same row. A row confirmed by
both sources with a capacity match can hit the 100 cap — that's the point.
When both sources contributed, a "Cross-confirmed (FMCSA + OSM)" chip is emitted
alongside the usual authority + DC chips.

Drop rules (applied before scoring):
  - `state` not in `IN_REGION_STATES` — defence in depth; the crawler already enforces
    this at ingest, but the ranker never trusts upstream (a row that leaks in must not
    surface to the operator).
  - No `name` — a nameless row has nothing to show; the OSM mapper is supposed to have
    synthesised a label ("Unnamed warehouse near <city>") for the two "obvious shipper"
    tags before we get here (see Slice 2b).
  - Promoted lead is ever `not_interested` in `call_outcomes` — we honour the same
    permanent-suppression rule the call list uses.

Filters (applied after scoring so filter narrows are consistent regardless of order):
  - `state`         — exact 2-letter code match (case-insensitive)
  - `source`        — 'FMCSA' | 'OSM' | 'Both'
                       * 'FMCSA' → "FMCSA" in row.sources (includes merged rows)
                       * 'OSM'   → "OSM" in row.sources   (includes merged rows)
                       * 'Both'  → both must be present (cross-confirmed rows only)
  - `min_score`     — score >= min_score
  - `promoted_only` — True → only promoted; False → only un-promoted; None → both
  - `q`             — case-insensitive substring on `name`
  - `limit`         — hard-capped at 200

Deterministic sort: (-score, id asc). Two calls with unchanged data → same list.

Authority note: the check reads `bool(c.fmcsa_dot or c.fmcsa_mc)` — the merge fn
keeps the denormalised `mc`/`dot` columns in sync, but the ranker reads the
source-key columns directly so a stray non-FMCSA row with an mc value copied
in can't fake authority. `_authority_label` falls back to `c.mc`/`c.dot` for
the display string only.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime

from app.models import CallOutcome, CapacityPost, ShipperCandidate
from app.shared.region import IN_REGION_STATES

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
    match_reason: str | None = None


@dataclass(frozen=True, slots=True)
class ShipperFilters:
    state: str | None = None
    source: str | None = None  # 'FMCSA' | 'OSM' | 'Both'
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
    `landuse=industrial` deliberately doesn't count here.
    """
    if not isinstance(osm_tags, dict):
        return None
    if osm_tags.get("industrial") == "distribution_centre":
        return "distribution_centre"
    if osm_tags.get("building") == "warehouse":
        return "warehouse"
    return None


def _authority_label(fmcsa_mc: str | None, fmcsa_dot: str | None, mc: str | None, dot: str | None) -> str:
    """Prefer the source-key columns; fall back to the denormalised mc/dot for defence in depth.

    The merge fn keeps them in sync, but the ranker never trusts upstream —
    if fmcsa_* is empty for any reason, we still render an honest label from
    what's on the row.
    """
    if fmcsa_mc or mc:
        return f"MC {fmcsa_mc or mc}"
    if fmcsa_dot or dot:
        return f"DOT {fmcsa_dot or dot}"
    return "authority"


def _row_sources(c: ShipperCandidate) -> tuple[str, ...]:
    """Read `sources` off a candidate as a deterministic tuple.

    JSONB list on Postgres, JSON list on SQLite — same shape. Empty / missing
    falls back to `()` so downstream filter logic is uniform.
    """
    raw = getattr(c, "sources", None)
    if not raw:
        return ()
    # Preserve insertion order — the merge fn writes FMCSA before OSM when both fire.
    return tuple(str(s) for s in raw)


# ---- main ------------------------------------------------------------------


def rank_shippers(
    candidates: Iterable[ShipperCandidate],
    posts: Iterable[CapacityPost],
    outcomes: Iterable[CallOutcome],
    today: date,  # reserved for future signals (staleness, recency); mirrors call_rank
    *,
    filters: ShipperFilters | None = None,
) -> list[ShipperRow]:
    """Score, filter, sort. Deterministic: same inputs → same outputs, always."""
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

        sources = _row_sources(c)

        # Score.
        score = 0
        reasons: list[str] = []

        # FMCSA authority — keyed on the source-key column, not `source == 'FMCSA'`.
        # A merged row with both FMCSA + OSM keeps its fmcsa_* set, so this fires
        # correctly on cross-confirmed rows.
        fmcsa_mc = getattr(c, "fmcsa_mc", None)
        fmcsa_dot = getattr(c, "fmcsa_dot", None)
        has_authority = bool(fmcsa_mc or fmcsa_dot)
        if has_authority:
            score += 40
            reasons.append(f"Registered shipper — {_authority_label(fmcsa_mc, fmcsa_dot, c.mc, c.dot)}")

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

        # Cross-confirmed chip — surface the trust signal the operator is buying.
        if "FMCSA" in sources and "OSM" in sources:
            reasons.append("Cross-confirmed (FMCSA + OSM)")

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
                sources=sources,
                mc=c.mc,
                dot=c.dot,
                domain=c.domain,
                phone=(c.phone or None),
                primary_email=(c.primary_email or None),
                score=score,
                reasons=tuple(reasons),
                promoted_lead_id=c.promoted_lead_id,
                match_reason=getattr(c, "match_reason", None),
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
        # 'FMCSA'/'OSM' match the label being present in sources (works for
        # single-source and merged rows). 'Both' requires both — the
        # cross-confirmed subset only.
        src = f.source.strip()
        src_up = src.upper()
        if src_up == "BOTH" or src.lower() == "both":
            out = [r for r in out if "FMCSA" in r.sources and "OSM" in r.sources]
        else:
            out = [r for r in out if src_up in r.sources]
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


# Kept for parity with call_rank's UTC-aware sentinel idiom — see 2026-09-29 call-list
# review: DTZ901 bug. A future signal that compares `first_seen_at` against a fallback
# should use this rather than bare `datetime.min`.
_DATETIME_MIN_UTC: datetime = datetime.min.replace(tzinfo=UTC)
