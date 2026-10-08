"""`app.pipeline.shipper_merge.match_and_merge` — pure, no DB, no network.

Table-driven per the plan's Slice 2a test matrix. Every case constructs an
`IncomingCandidate` and a small list of existing rows, calls the fn, asserts
the returned `MergeDecision`.

Wrong merges are worse than duplicates — most cases here assert that ambiguous
inputs deliberately return `MergeDecision.new()`, not merge.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.prospecting.pipeline.shipper_merge import (
    IncomingCandidate,
    _normalize_domain,
    match_and_merge,
    normalize_name,
)


@dataclass
class _Row:
    """Minimal existing-row double. The merge fn reads via `_field` so any object
    exposing these attributes works — including a real `ShipperCandidate`.
    """

    id: str
    sources: list[str]
    name: str
    state: str
    city: str | None = None
    phone: str | None = None
    domain: str | None = None
    fmcsa_dot: str | None = None
    fmcsa_mc: str | None = None
    osm_ref: str | None = None
    extra: dict = field(default_factory=dict)


# ---- normalize_name --------------------------------------------------------


def test_normalize_name_strips_corp_suffixes():
    # All four variants collapse to the same normalized form.
    assert (
        normalize_name("Acme Inc.")
        == normalize_name("Acme Incorporated")
        == normalize_name("ACME, INC")
        == normalize_name("Acme")
        == "acme"
    )


def test_normalize_name_iteratively_strips_multiple_suffixes():
    # "Acme Co Inc" strips to "Acme Co" strips to "Acme".
    assert normalize_name("Acme Co Inc") == "acme"


def test_normalize_name_llc_variants():
    assert normalize_name("Acme LLC") == normalize_name("Acme L.L.C.") == "acme"


def test_normalize_name_unicode_folds_via_nfkd():
    # "Müller Distribution" and "Muller Distribution" normalize equal.
    assert normalize_name("Müller Distribution") == normalize_name("Muller Distribution") == "muller distribution"


def test_normalize_name_returns_empty_on_blank_or_none():
    assert normalize_name(None) == ""
    assert normalize_name("") == ""
    assert normalize_name("   ") == ""


# ---- free-mail domain exclusion --------------------------------------------


def test_normalize_domain_returns_empty_for_free_mail_providers():
    # Free-mail domains must not corroborate a company match.
    for domain in ("gmail.com", "yahoo.com", "outlook.com", "hotmail.com", "icloud.com"):
        assert _normalize_domain(domain) == "", f"expected '' for {domain}"
    # www-prefixed free-mail also excluded.
    assert _normalize_domain("www.gmail.com") == ""


def test_normalize_domain_passes_through_real_business_domains():
    assert _normalize_domain("acme.com") == "acme.com"
    assert _normalize_domain("www.acme.com") == "acme.com"
    assert _normalize_domain("logistics.io") == "logistics.io"


def test_do_not_merge_when_domain_corroborator_is_free_mail():
    # Two different companies with the same normalized name + state, same city,
    # both using gmail.com — should NOT merge.
    existing = [
        _Row(
            id="F",
            sources=["FMCSA"],
            name="ABC Logistics LLC",
            state="NJ",
            city="Kearny",
            domain="gmail.com",
            fmcsa_mc="1",
        )
    ]
    incoming = IncomingCandidate(
        source="OSM",
        name="ABC Logistics",
        state="NJ",
        city="Kearny",
        domain="gmail.com",
        osm_ref="way/99",
    )
    # City matches → should merge via name+city, NOT name+domain.
    # (The city corroborator fires first and is valid; only the domain path is
    # suppressed for free-mail. This test uses different cities to force domain.)
    # Retry with different city so domain is the only candidate:
    match_and_merge(existing, incoming)  # called for coverage; city path wins here
    existing2 = [
        _Row(
            id="F2",
            sources=["FMCSA"],
            name="ABC Logistics LLC",
            state="NJ",
            city="Newark",
            domain="gmail.com",
            fmcsa_mc="2",
        )
    ]
    incoming2 = IncomingCandidate(
        source="OSM",
        name="ABC Logistics",
        state="NJ",
        city="Kearny",  # different city — city corroborator fails
        domain="gmail.com",  # free-mail — domain corroborator must also fail
        osm_ref="way/100",
    )
    decision2 = match_and_merge(existing2, incoming2)
    assert decision2.action == "new", "free-mail domain must not corroborate a merge"


# ---- rule 1: exact source-key hit ------------------------------------------


def test_merge_same_fmcsa_mc_on_repeat_fmcsa_sighting():
    existing = [_Row(id="A", sources=["FMCSA"], name="Acme", state="NJ", fmcsa_mc="123")]
    incoming = IncomingCandidate(source="FMCSA", name="Whatever Name", state="NJ", fmcsa_mc="123")
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.target_id == "A"
    assert decision.match_reason == "same fmcsa_mc"


def test_merge_same_fmcsa_dot_on_repeat_fmcsa_sighting():
    existing = [_Row(id="A", sources=["FMCSA"], name="Acme", state="NJ", fmcsa_dot="9")]
    incoming = IncomingCandidate(source="FMCSA", name="Different Name", state="NJ", fmcsa_dot="9")
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.target_id == "A"
    assert decision.match_reason == "same fmcsa_dot"


def test_merge_same_osm_ref_on_repeat_osm_sighting():
    existing = [_Row(id="B", sources=["OSM"], name="Acme Whs", state="NJ", osm_ref="way/42")]
    incoming = IncomingCandidate(source="OSM", name="acme warehouse", state="NJ", osm_ref="way/42")
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.target_id == "B"
    assert decision.match_reason == "same osm_ref"


# ---- rule 2: cross-source match --------------------------------------------


def test_merge_cross_source_on_name_plus_city():
    existing = [
        _Row(
            id="F",
            sources=["FMCSA"],
            name="Acme Distribution LLC",
            state="NJ",
            city="Newark",
            fmcsa_mc="123",
        )
    ]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        city="Newark",
        osm_ref="way/1",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.target_id == "F"
    assert decision.match_reason == "name+city"


def test_merge_cross_source_on_name_plus_phone_different_cities():
    existing = [
        _Row(
            id="F",
            sources=["FMCSA"],
            name="Acme Distribution LLC",
            state="NJ",
            city="Newark",
            phone="555-123-4567",
            fmcsa_mc="123",
        )
    ]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        city="Kearny",  # different city → city corroborator fails
        phone="+1 555 123 4567",  # same digits after normalization
        osm_ref="way/1",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.match_reason == "name+phone"


def test_merge_cross_source_on_name_plus_domain_different_cities():
    existing = [
        _Row(
            id="F",
            sources=["FMCSA"],
            name="Acme Distribution LLC",
            state="NJ",
            city="Newark",
            domain="acme.com",
            fmcsa_mc="123",
        )
    ]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        city="Kearny",
        domain="www.acme.com",  # www prefix normalized off
        osm_ref="way/1",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.match_reason == "name+domain"


def test_city_wins_over_phone_and_domain_when_multiple_corroborators_fire():
    # Determinism: city > phone > domain in the check order.
    existing = [
        _Row(
            id="F",
            sources=["FMCSA"],
            name="Acme Distribution LLC",
            state="NJ",
            city="Newark",
            phone="555-123-4567",
            domain="acme.com",
            fmcsa_mc="1",
        )
    ]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        city="Newark",
        phone="+1 555 123 4567",
        domain="acme.com",
        osm_ref="way/9",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.match_reason == "name+city"


# ---- rule 3: do-not-merge cases (the conservatism) -------------------------


def test_do_not_merge_when_states_differ():
    existing = [_Row(id="F", sources=["FMCSA"], name="Acme Distribution LLC", state="NJ", city="Newark", fmcsa_mc="1")]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NY",  # different state
        city="Newark",
        osm_ref="way/2",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "new"


def test_do_not_merge_when_no_corroborator_fires():
    # Same normalized name + same state, but different city and no phone/domain.
    existing = [_Row(id="F", sources=["FMCSA"], name="Acme Distribution LLC", state="NJ", city="Newark", fmcsa_mc="1")]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        city="Kearny",
        osm_ref="way/3",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "new"


def test_do_not_merge_two_fmcsa_rows_with_same_name_different_dots():
    # Both FMCSA — cross-source rule doesn't apply (counterpart absent);
    # source-key rule (1) misses because DOTs differ. Result: new row.
    existing = [_Row(id="F1", sources=["FMCSA"], name="Acme Distribution LLC", state="NJ", fmcsa_dot="1")]
    incoming = IncomingCandidate(
        source="FMCSA",
        name="Acme Distribution LLC",
        state="NJ",
        fmcsa_dot="2",  # different authority → distinct company
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "new"


def test_do_not_merge_two_osm_rows_with_same_name_different_refs():
    existing = [_Row(id="O1", sources=["OSM"], name="acme distribution", state="NJ", osm_ref="way/1")]
    incoming = IncomingCandidate(
        source="OSM",
        name="acme distribution",
        state="NJ",
        osm_ref="way/2",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "new"


def test_do_not_merge_when_incoming_name_is_blank():
    existing = [_Row(id="F", sources=["FMCSA"], name="Acme", state="NJ", city="Newark", fmcsa_mc="1")]
    incoming = IncomingCandidate(source="OSM", name="", state="NJ", city="Newark", osm_ref="way/1")
    decision = match_and_merge(existing, incoming)
    assert decision.action == "new"


# ---- exact-hit shortcut supersedes cross-source ---------------------------


def test_source_key_hit_wins_over_name_mismatch():
    # Even a completely different name merges when the source key matches —
    # same authority = same company, regardless of how the name is written.
    existing = [_Row(id="F", sources=["FMCSA"], name="Old Corporate Name", state="NJ", fmcsa_mc="123")]
    incoming = IncomingCandidate(
        source="FMCSA",
        name="Totally Different Legal Name",
        state="PA",  # different state doesn't block the source-key hit
        fmcsa_mc="123",
    )
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.match_reason == "same fmcsa_mc"


# ---- merged rows are re-mergeable via cross-source ------------------------


def test_incoming_matches_already_merged_row_via_source_key():
    # A row with sources=['FMCSA','OSM'] and both keys set — the incoming
    # sighting hits one of them and merges again.
    existing = [
        _Row(
            id="M",
            sources=["FMCSA", "OSM"],
            name="Acme",
            state="NJ",
            fmcsa_mc="1",
            osm_ref="way/1",
        )
    ]
    incoming = IncomingCandidate(source="OSM", name="acme", state="NJ", osm_ref="way/1")
    decision = match_and_merge(existing, incoming)
    assert decision.action == "merge"
    assert decision.target_id == "M"
    assert decision.match_reason == "same osm_ref"
