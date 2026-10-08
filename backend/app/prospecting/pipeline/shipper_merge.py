"""Pure merge rule for `shipper_candidates` — the "one row per company" contract.

Decides whether an incoming FMCSA or OSM sighting should merge into an existing
row or start a new one. No DB, no network, no dependencies on SQLAlchemy — the
caller (Slice 2b's `pipeline/run.py`) passes the current in-region rows in as a
list, and this module returns a `MergeDecision`.

The rule is deliberately **conservative** (see plan amendment): wrong merges
are worse than duplicates. A false merge glues two real companies together
permanently; a false split just costs one extra "Already in your leads" toast
when the operator promotes.

Confident-match rule (all must hold to merge):

  1. **Exact source-key hit.** If the incoming carries `fmcsa_dot`, `fmcsa_mc`,
     or `osm_ref` and an existing row already has that exact key → merge.
     Same authority = same company; name doesn't matter.

  2. **Cross-source match** (FMCSA ↔ OSM), applied only when (1) misses:
       - same `state` (2-letter, upper), AND
       - `normalize_name(a) == normalize_name(b)` (non-empty on both sides), AND
       - at least one corroborator:
           * same normalized `city` (lowercased, trimmed, non-empty on both), OR
           * matching `phone` (digits-only ≥10, leading '1' stripped), OR
           * matching `domain` (lowercased, `www.` stripped, non-empty on both).
     `match_reason` records which corroborator won, in deterministic order:
     `name+city` → `name+phone` → `name+domain`.

  3. Otherwise → new row. Ambiguity (same normalized name + state, no
     corroborator) is deliberately treated as two rows.

Tested in isolation — see `tests/test_shipper_merge.py`.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any

# ---- normalizers -----------------------------------------------------------

# Corporate suffixes to strip from the tail of a name during normalization. The
# list is deliberately conservative — we normalize "Acme Inc." and "Acme, Inc"
# to the same string, but we don't try to normalize "Acme Manufacturing" and
# "Acme Mfg" (that's a different problem — abbreviation, not suffix).
_CORP_SUFFIXES: tuple[str, ...] = (
    "incorporated",
    "corporation",
    "limited",
    "company",
    "pllc",
    "llc",
    "ltd",
    "inc",
    "corp",
    "co",
    "pc",
)

# Match a trailing corporate suffix, optionally preceded by a comma and/or
# whitespace, optionally with trailing dots ("L.L.C." after we strip dots
# becomes "llc"). Applied iteratively so "Acme Co Inc" strips to "Acme".
_SUFFIX_RE = re.compile(
    r"[\s,]*\b(?:" + "|".join(re.escape(s) for s in _CORP_SUFFIXES) + r")\.?\s*$",
    re.IGNORECASE,
)


def normalize_name(s: str | None) -> str:
    """Deterministic, unicode-safe name normalization for merge comparisons.

    Steps: NFKD-normalize (folds "Müller" → "Muller"), lowercase, strip
    punctuation, iteratively strip trailing corporate suffixes, collapse
    whitespace. Returns "" for None or blank input — the caller must treat
    an empty normalized name as "does not match".
    """
    if not s:
        return ""
    # NFKD folds accented characters; encode-decode to ASCII drops the
    # combining marks. "Müller" → "Muller".
    folded = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    lowered = folded.lower()
    # Dots go without leaving whitespace so "L.L.C." → "llc" (matches the suffix regex);
    # other punctuation collapses to whitespace so "Acme, Inc" → "acme inc".
    no_dots = lowered.replace(".", "")
    stripped = re.sub(r"[^\w\s]", " ", no_dots)
    # Iteratively strip trailing corp suffixes ("Acme Co Inc" → "Acme Co" → "Acme").
    prev = None
    cur = stripped.strip()
    while prev != cur:
        prev = cur
        cur = _SUFFIX_RE.sub("", cur).strip()
    # Collapse internal whitespace.
    return re.sub(r"\s+", " ", cur).strip()


def _normalize_city(s: str | None) -> str:
    return (s or "").strip().lower()


def _normalize_phone(s: str | None) -> str:
    """Digits-only, strip leading '1' (US country code). Returns '' if <10 digits."""
    if not s:
        return ""
    digits = re.sub(r"\D+", "", s)
    if len(digits) < 10:
        return ""
    if len(digits) == 11 and digits.startswith("1"):
        digits = digits[1:]
    return digits


# Free-mail providers must never corroborate a company match — two different companies
# sharing a gmail/yahoo/outlook address would produce a false merge. Return "" so the
# domain corroborator treats free-mail the same as no domain at all.
FREE_MAIL_DOMAINS: frozenset[str] = frozenset(
    {
        "gmail.com",
        "googlemail.com",
        "yahoo.com",
        "yahoo.co.uk",
        "hotmail.com",
        "outlook.com",
        "live.com",
        "msn.com",
        "aol.com",
        "icloud.com",
        "me.com",
        "mac.com",
        "protonmail.com",
        "proton.me",
    }
)


def _normalize_domain(s: str | None) -> str:
    if not s:
        return ""
    d = s.strip().lower().removeprefix("www.")
    if d in FREE_MAIL_DOMAINS:
        return ""
    return d


# ---- decision type ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class IncomingCandidate:
    """The shape the merge fn expects from an incoming sighting.

    Kept dependency-free (no SQLAlchemy) so it's trivially constructible in
    tests and in the ingest layer (Slice 2b). Field names mirror
    `ShipperCandidate` so `pipeline/run.py` can build one straight from the
    upstream FMCSA row or Overpass element without a translation layer.
    """

    source: str  # 'FMCSA' | 'OSM'
    name: str
    state: str
    city: str | None = None
    fmcsa_dot: str | None = None
    fmcsa_mc: str | None = None
    osm_ref: str | None = None
    phone: str | None = None
    domain: str | None = None
    # For pass-through into evidence / other columns; the merge fn itself
    # ignores these.
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class MergeDecision:
    """The outcome of `match_and_merge`.

    `action == "merge"` → merge into `target_id`; `match_reason` explains why.
    `action == "new"`   → insert a new row; `match_reason` is None.
    """

    action: str  # 'merge' | 'new'
    target_id: str | None = None
    match_reason: str | None = None

    @classmethod
    def merge(cls, target_id: str, match_reason: str) -> MergeDecision:
        return cls(action="merge", target_id=target_id, match_reason=match_reason)

    @classmethod
    def new(cls) -> MergeDecision:
        return cls(action="new")


# ---- existing-row shape ----------------------------------------------------
#
# The merge fn reads only a handful of fields off each existing row. We accept
# either a `ShipperCandidate` ORM instance or any object exposing the same
# attributes (a dict-like, a dataclass, a test double). The `_field` helper
# tolerates both.


def _field(row: Any, name: str) -> Any:
    if isinstance(row, dict):
        return row.get(name)
    return getattr(row, name, None)


# ---- main ------------------------------------------------------------------


def match_and_merge(existing: list[Any], incoming: IncomingCandidate) -> MergeDecision:
    """Decide whether `incoming` merges into one of `existing`, or starts new.

    Pure: no DB, no network, no I/O. Same inputs → same output.

    Determinism note: rule (1) exits on the first source-key hit; source-key
    columns are partial-UNIQUE on the DB so at most one row can match.
    Rule (2) scans in list order and returns on the first corroborator match;
    if the caller passes rows in a stable order (e.g. by id) the result is
    stable. In practice the pipeline stages incoming candidates one at a time,
    so this is a straight-line decision.
    """
    # ---- (1) exact source-key hit ------------------------------------------
    if incoming.fmcsa_dot:
        for row in existing:
            if _field(row, "fmcsa_dot") == incoming.fmcsa_dot:
                return MergeDecision.merge(_field(row, "id"), "same fmcsa_dot")
    if incoming.fmcsa_mc:
        for row in existing:
            if _field(row, "fmcsa_mc") == incoming.fmcsa_mc:
                return MergeDecision.merge(_field(row, "id"), "same fmcsa_mc")
    if incoming.osm_ref:
        for row in existing:
            if _field(row, "osm_ref") == incoming.osm_ref:
                return MergeDecision.merge(_field(row, "id"), "same osm_ref")

    # ---- (2) cross-source match --------------------------------------------
    # Only FMCSA ↔ OSM pairings are eligible. Two FMCSA rows with the same
    # normalized name but different DOTs are already-distinct authorities;
    # merging them would be wrong. Same for two OSM refs.
    in_norm_name = normalize_name(incoming.name)
    if not in_norm_name:
        return MergeDecision.new()

    in_state = (incoming.state or "").upper().strip()
    if not in_state:
        return MergeDecision.new()

    in_source = (incoming.source or "").upper()
    in_city = _normalize_city(incoming.city)
    in_phone = _normalize_phone(incoming.phone)
    in_domain = _normalize_domain(incoming.domain)

    for row in existing:
        row_sources = _field(row, "sources") or []
        # Cross-source only. GEMINI is the third source (scope change 2026-09-30);
        # it may merge into an FMCSA or OSM row on name+state+corroborator, but two
        # rows of the same source require a source-key match (rule 1).
        if in_source == "FMCSA":
            counterparts = {"OSM", "GEMINI"}
        elif in_source == "OSM":
            counterparts = {"FMCSA", "GEMINI"}
        else:  # GEMINI
            counterparts = {"FMCSA", "OSM"}
        if not any(cp in row_sources for cp in counterparts):
            continue

        # Same state.
        row_state = (_field(row, "state") or "").upper().strip()
        if row_state != in_state:
            continue

        # Same normalized name.
        row_name_norm = normalize_name(_field(row, "name"))
        if not row_name_norm or row_name_norm != in_norm_name:
            continue

        # At least one corroborator, checked in deterministic order.
        row_city = _normalize_city(_field(row, "city"))
        if in_city and row_city and in_city == row_city:
            return MergeDecision.merge(_field(row, "id"), "name+city")

        row_phone = _normalize_phone(_field(row, "phone"))
        if in_phone and row_phone and in_phone == row_phone:
            return MergeDecision.merge(_field(row, "id"), "name+phone")

        row_domain = _normalize_domain(_field(row, "domain"))
        if in_domain and row_domain and in_domain == row_domain:
            return MergeDecision.merge(_field(row, "id"), "name+domain")

        # Name + state match but no corroborator — ambiguous. Do not merge.

    # ---- (3) otherwise --------------------------------------------------
    return MergeDecision.new()
