"""Deterministic Shipper-Finder ranker — table-driven, no Gemini, no DB.

Mirrors the style of test_call_rank.py: build ORM objects in memory, call the pure
function, assert on the shape of the output. Also asserts the model + migration
smoke: `shipper_candidates` is registered on `Base.metadata` with the expected
columns and indexes.

Slice 2a update: candidates now use the merged-row shape — `sources` list +
`fmcsa_mc`/`fmcsa_dot`/`osm_ref` source-key columns, no `source` column.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.db import Base
from app.models import CallOutcome, CapacityPost, ShipperCandidate
from app.pipeline.shipper_rank import (
    SHIPPER_FINDER_LIMIT_CAP,
    ShipperFilters,
    rank_shippers,
)

TODAY = date(2026, 9, 30)


# ---- factories -------------------------------------------------------------


def _cand(**kw) -> ShipperCandidate:
    """Build a FMCSA candidate by default. Override `sources` + source-key columns
    to make OSM or merged rows.

    The factory sets the denormalised `mc`/`dot` from `fmcsa_mc`/`fmcsa_dot` when
    the caller doesn't override — matches what the merge fn writes.
    """
    fmcsa_mc = kw.pop("fmcsa_mc", "1") if "fmcsa_mc" in kw or "sources" not in kw else None
    # When caller passes sources explicitly (e.g. OSM-only), let them drive
    # fmcsa_* + mc/dot. Otherwise default to a valid FMCSA row.
    sources = kw.pop("sources", ["FMCSA"])
    defaults = {
        "id": "01ABCDEF00000000000000000A",
        "sources": sources,
        "fmcsa_mc": fmcsa_mc,
        "fmcsa_dot": None,
        "osm_ref": None,
        "match_reason": None,
        "name": "Acme Manufacturing Inc",
        "state": "NJ",
        "city": "Newark",
        "address": None,
        "lat": None,
        "lng": None,
        "mc": fmcsa_mc,
        "dot": None,
        "domain": None,
        "phone": None,
        "primary_email": None,
        "osm_tags": None,
        "raw": None,
        "evidence": None,
        "promoted_lead_id": None,
    }
    defaults.update(kw)
    # Keep denormalised mc/dot in sync with fmcsa_* if caller didn't override.
    if "mc" not in kw and defaults["fmcsa_mc"] is not None:
        defaults["mc"] = defaults["fmcsa_mc"]
    if "dot" not in kw and defaults["fmcsa_dot"] is not None:
        defaults["dot"] = defaults["fmcsa_dot"]
    return ShipperCandidate(**defaults)


def _osm(**kw) -> ShipperCandidate:
    """Convenience: build an OSM-only candidate."""
    base = {
        "id": "01ABCDEF00000000000000000O",
        "sources": ["OSM"],
        "fmcsa_mc": None,
        "fmcsa_dot": None,
        "mc": None,
        "dot": None,
        "osm_ref": "way/1",
        "osm_tags": {},
    }
    base.update(kw)
    return _cand(**base)


def _post(**kw) -> CapacityPost:
    defaults = {
        "id": "CP-1",
        "kind": "truck",
        "equipment": "Dry Van",
        "origin_state": "NJ",
        "destinations": ["PA", "GA"],
        "status": "open",
    }
    defaults.update(kw)
    return CapacityPost(**defaults)


def _outcome(lead_id: str, outcome: str, *, days_ago: int = 0) -> CallOutcome:
    logged = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=UTC)
    return CallOutcome(lead_id=lead_id, outcome=outcome, logged_at=logged)


# ---- signals ---------------------------------------------------------------


def test_fmcsa_authority_fires_when_fmcsa_mc_set():
    c = _cand(fmcsa_mc="123456", fmcsa_dot=None)
    rows = rank_shippers([c], [], [], TODAY)
    assert len(rows) == 1
    assert rows[0].score == 40
    assert "Registered shipper — MC 123456" in rows[0].reasons


def test_fmcsa_authority_falls_back_to_dot_when_no_mc():
    c = _cand(id="01ABCDEF00000000000000000D", fmcsa_mc=None, fmcsa_dot="9")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 40
    assert "Registered shipper — DOT 9" in rows[0].reasons


def test_fmcsa_authority_does_not_fire_without_source_keys():
    # A row with sources=['FMCSA'] but no fmcsa_mc/fmcsa_dot earns nothing.
    c = _cand(fmcsa_mc=None, fmcsa_dot=None, mc=None, dot=None)
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0
    assert not any("Registered shipper" in r for r in rows[0].reasons)


def test_osm_only_row_does_not_earn_fmcsa_authority():
    # OSM-only rows have no fmcsa_* → no authority bonus even if legacy mc slipped in.
    c = _osm()
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0


def test_capacity_post_match_adds_25_with_lane_chip():
    c = _osm(state="NJ")
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 25
    assert "Fits your NJ→PA truck" in rows[0].reasons


def test_capacity_load_post_matches_dest_state():
    c = _osm(id="01ABCDEF00000000000000000G", state="GA")
    post = _post(kind="load", origin_state="NJ", dest_state="GA", destinations=[])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 25
    assert "Fits your NJ→GA load" in rows[0].reasons


def test_closed_post_does_not_match():
    c = _osm()
    post = _post(status="closed")
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 0
    assert not any("Fits your" in r for r in rows[0].reasons)


def test_phone_adds_15():
    c = _osm(phone="5551234567")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 15


def test_email_adds_10():
    c = _osm(primary_email="ops@acme.example")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10


def test_osm_named_dc_tag_adds_10():
    c = _osm(osm_tags={"industrial": "distribution_centre"})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10
    assert "Named DC — distribution_centre" in rows[0].reasons


def test_osm_named_warehouse_tag_adds_10():
    c = _osm(osm_tags={"building": "warehouse"})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10
    assert "Named DC — warehouse" in rows[0].reasons


def test_generic_landuse_industrial_earns_no_bonus():
    c = _osm(osm_tags={"landuse": "industrial"})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0
    assert not any("Named DC" in r for r in rows[0].reasons)


def test_already_promoted_gets_5_nudge():
    c = _osm(promoted_lead_id="MC-99")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 5


def test_signals_stack_and_cap_at_100_single_source():
    # 40 (FMCSA) + 25 (post) + 15 (phone) + 10 (email) + 5 (promoted) = 95 — under 100.
    c = _cand(
        fmcsa_mc="123",
        state="NJ",
        phone="5551234567",
        primary_email="ops@acme.example",
        promoted_lead_id="MC-1",
    )
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 95


def test_merged_row_hits_100_cap_with_both_sources_and_capacity_match():
    # Merged row: FMCSA authority (+40) + OSM named-DC tag (+10) + capacity match (+25)
    # + phone (+15) + email (+10) + promoted (+5) = 105 → clamp to 100.
    c = _cand(
        id="01ABCDEF00000000000000000M",
        sources=["FMCSA", "OSM"],
        fmcsa_mc="123",
        osm_ref="way/42",
        state="NJ",
        phone="5551234567",
        primary_email="ops@acme.example",
        promoted_lead_id="MC-1",
        osm_tags={"building": "warehouse"},
        match_reason="name+city",
    )
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 100
    # Cross-confirmed chip fires on merged rows.
    assert "Cross-confirmed (FMCSA + OSM)" in rows[0].reasons
    # Authority + DC chips still present.
    assert any("Registered shipper — MC 123" in r for r in rows[0].reasons)
    assert any("Named DC — warehouse" in r for r in rows[0].reasons)
    # match_reason surfaces on the row for the frontend's evidence panel.
    assert rows[0].match_reason == "name+city"


def test_cross_confirmed_chip_only_on_merged_rows():
    single = _cand(fmcsa_mc="1")
    rows = rank_shippers([single], [], [], TODAY)
    assert not any("Cross-confirmed" in r for r in rows[0].reasons)


# ---- drop rules ------------------------------------------------------------


def test_out_of_region_state_drops():
    c = _cand(state="TX")  # not in the 32-state footprint
    assert rank_shippers([c], [], [], TODAY) == []


def test_empty_name_drops():
    c = _cand(name="")
    assert rank_shippers([c], [], [], TODAY) == []


def test_promoted_lead_ever_not_interested_drops():
    c = _cand(fmcsa_mc="123", promoted_lead_id="MC-123")
    ni = _outcome("MC-123", "not_interested", days_ago=400)
    assert rank_shippers([c], [], [ni], TODAY) == []


def test_unpromoted_row_is_not_affected_by_outcomes():
    c = _cand(fmcsa_mc="123", promoted_lead_id=None)
    ni = _outcome("MC-123", "not_interested", days_ago=1)
    rows = rank_shippers([c], [], [ni], TODAY)
    assert len(rows) == 1


# ---- filters ---------------------------------------------------------------


def test_filter_state_narrows_predictably():
    a = _osm(id="A", state="NJ")
    b = _osm(id="B", state="PA")
    rows = rank_shippers([a, b], [], [], TODAY, filters=ShipperFilters(state="pa"))
    assert [r.id for r in rows] == ["B"]


def test_filter_source_fmcsa_matches_merged_rows():
    fmcsa_only = _cand(id="F", fmcsa_mc="1")
    osm_only = _osm(id="O")
    merged = _cand(id="M", sources=["FMCSA", "OSM"], fmcsa_mc="2", osm_ref="way/9")
    rows = rank_shippers(
        [fmcsa_only, osm_only, merged],
        [],
        [],
        TODAY,
        filters=ShipperFilters(source="FMCSA"),
    )
    assert {r.id for r in rows} == {"F", "M"}


def test_filter_source_osm_matches_merged_rows():
    fmcsa_only = _cand(id="F", fmcsa_mc="1")
    osm_only = _osm(id="O")
    merged = _cand(id="M", sources=["FMCSA", "OSM"], fmcsa_mc="2", osm_ref="way/9")
    rows = rank_shippers(
        [fmcsa_only, osm_only, merged],
        [],
        [],
        TODAY,
        filters=ShipperFilters(source="OSM"),
    )
    assert {r.id for r in rows} == {"O", "M"}


def test_filter_source_both_only_merged_rows():
    fmcsa_only = _cand(id="F", fmcsa_mc="1")
    osm_only = _osm(id="O")
    merged = _cand(id="M", sources=["FMCSA", "OSM"], fmcsa_mc="2", osm_ref="way/9")
    rows = rank_shippers(
        [fmcsa_only, osm_only, merged],
        [],
        [],
        TODAY,
        filters=ShipperFilters(source="Both"),
    )
    assert [r.id for r in rows] == ["M"]


def test_filter_min_score_narrows_predictably():
    hi = _cand(id="HI", fmcsa_mc="1")  # score 40
    lo = _osm(id="LO")  # score 0
    rows = rank_shippers([hi, lo], [], [], TODAY, filters=ShipperFilters(min_score=10))
    assert [r.id for r in rows] == ["HI"]


def test_filter_promoted_only_true_and_false():
    promoted = _cand(id="P", fmcsa_mc="1", promoted_lead_id="MC-1")
    unpromoted = _osm(id="U")
    both = [promoted, unpromoted]
    only_p = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=True))
    only_u = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=False))
    everything = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=None))
    assert [r.id for r in only_p] == ["P"]
    assert [r.id for r in only_u] == ["U"]
    assert {r.id for r in everything} == {"P", "U"}


def test_filter_q_case_insensitive_substring():
    a = _osm(id="A", name="Acme Manufacturing")
    b = _osm(id="B", name="Zenith Warehousing")
    rows = rank_shippers([a, b], [], [], TODAY, filters=ShipperFilters(q="acme"))
    assert [r.id for r in rows] == ["A"]


def test_filters_combine():
    a = _cand(id="A", state="NJ", fmcsa_mc="1", name="Acme")  # score 40, state NJ
    b = _cand(id="B", state="PA", fmcsa_mc="2", name="Acme")  # score 40, state PA
    c = _osm(id="C", state="NJ", name="Acme")  # 0
    rows = rank_shippers(
        [a, b, c],
        [],
        [],
        TODAY,
        filters=ShipperFilters(state="NJ", source="FMCSA", min_score=10, q="ac"),
    )
    assert [r.id for r in rows] == ["A"]


# ---- pagination + determinism ---------------------------------------------


def test_limit_caps_result_set():
    cands = [_cand(id=f"F{i:04d}", fmcsa_mc=str(i)) for i in range(120)]
    rows = rank_shippers(cands, [], [], TODAY, filters=ShipperFilters(limit=50))
    assert len(rows) == 50


def test_limit_is_hard_capped_at_200():
    cands = [_cand(id=f"F{i:04d}", fmcsa_mc=str(i)) for i in range(250)]
    rows = rank_shippers(cands, [], [], TODAY, filters=ShipperFilters(limit=10_000))
    assert len(rows) == SHIPPER_FINDER_LIMIT_CAP


def test_default_limit_is_50():
    cands = [_cand(id=f"F{i:04d}", fmcsa_mc=str(i)) for i in range(120)]
    rows = rank_shippers(cands, [], [], TODAY)
    assert len(rows) == 50


def test_deterministic_same_input_same_output():
    cands = [
        _cand(id=f"F{i:03d}", fmcsa_mc=str(i), name=f"Shipper {i}", state="NJ" if i % 2 else "PA") for i in range(20)
    ]
    a = rank_shippers(cands, [], [], TODAY)
    b = rank_shippers(cands, [], [], TODAY)
    assert [(r.id, r.score, r.reasons) for r in a] == [(r.id, r.score, r.reasons) for r in b]


def test_deterministic_tiebreak_is_id_ascending():
    a = _osm(id="OSM-Z")
    b = _osm(id="OSM-A")
    rows = rank_shippers([a, b], [], [], TODAY)
    assert [r.id for r in rows] == ["OSM-A", "OSM-Z"]


def test_row_carries_all_display_fields():
    c = _cand(
        id="01ABCDEF00000000000000000F",
        fmcsa_mc="1",
        name="Acme Inc",
        state="NJ",
        city="Newark",
        address="1 Main St",
        lat=40.7,
        lng=-74.2,
        phone="5551234567",
        primary_email="ops@acme.example",
        domain="acme.example",
    )
    rows = rank_shippers([c], [], [], TODAY)
    r = rows[0]
    assert r.name == "Acme Inc"
    assert r.state == "NJ"
    assert r.city == "Newark"
    assert r.address == "1 Main St"
    assert r.lat == 40.7 and r.lng == -74.2
    assert r.sources == ("FMCSA",)
    assert r.phone == "5551234567"
    assert r.primary_email == "ops@acme.example"
    assert r.domain == "acme.example"
    assert r.mc == "1"


def test_row_sources_tuple_preserves_insertion_order_on_merged():
    c = _cand(id="M", sources=["FMCSA", "OSM"], fmcsa_mc="1", osm_ref="way/1")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].sources == ("FMCSA", "OSM")


# ---- model + migration smoke ----------------------------------------------


def test_shipper_candidates_registered_on_metadata():
    """Smoke: the model is imported and the table sits on Base.metadata with the
    columns migrations 0004 + 0005 create. Prevents drift between model and
    migration without touching a real DB.
    """
    t = Base.metadata.tables.get("shipper_candidates")
    assert t is not None
    cols = set(t.columns.keys())
    expected_cols = {
        "id",
        "sources",
        "fmcsa_dot",
        "fmcsa_mc",
        "osm_ref",
        "match_reason",
        "name",
        "state",
        "city",
        "address",
        "lat",
        "lng",
        "mc",
        "dot",
        "domain",
        "phone",
        "primary_email",
        "osm_tags",
        "raw",
        "evidence",
        "promoted_lead_id",
        "first_seen_at",
        "last_seen_at",
    }
    assert expected_cols.issubset(cols)
    # `source` (old scalar) is gone after 0005.
    assert "source" not in cols
    # PK is `id`.
    assert [c.name for c in t.primary_key.columns] == ["id"]
    # NOT NULL invariants (post-0005 reshape).
    assert not t.columns["sources"].nullable
    assert not t.columns["name"].nullable
    assert not t.columns["state"].nullable
    # Nullable business columns.
    assert t.columns["promoted_lead_id"].nullable
    assert t.columns["fmcsa_dot"].nullable
    assert t.columns["fmcsa_mc"].nullable
    assert t.columns["osm_ref"].nullable
    assert t.columns["match_reason"].nullable
    # Indexes present (name only — the migration owns the on-disk shape).
    index_names = {ix.name for ix in t.indexes}
    assert {
        "shipper_candidates_state",
        "shipper_candidates_unpromoted",
        "shipper_candidates_fmcsa_dot_key",
        "shipper_candidates_fmcsa_mc_key",
        "shipper_candidates_osm_ref_key",
    }.issubset(index_names)
    # The old `source` index is gone.
    assert "shipper_candidates_source" not in index_names
