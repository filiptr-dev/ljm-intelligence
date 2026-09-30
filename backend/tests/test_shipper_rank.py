"""Deterministic Shipper-Finder ranker — table-driven, no Gemini, no DB.

Mirrors the style of test_call_rank.py: build ORM objects in memory, call the pure
function, assert on the shape of the output. Also asserts the model + migration
smoke: `shipper_candidates` is registered on `Base.metadata` with the expected
columns and indexes.
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
    defaults = {
        "id": "FMCSA-MC-1",
        "source": "FMCSA",
        "name": "Acme Manufacturing Inc",
        "state": "NJ",
        "city": "Newark",
        "address": None,
        "lat": None,
        "lng": None,
        "mc": "1",
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
    return ShipperCandidate(**defaults)


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


def test_fmcsa_authority_fires_when_source_and_mc():
    c = _cand(source="FMCSA", mc="123456", dot=None)
    rows = rank_shippers([c], [], [], TODAY)
    assert len(rows) == 1
    assert rows[0].score == 40
    assert "Registered shipper — MC 123456" in rows[0].reasons


def test_fmcsa_authority_falls_back_to_dot_when_no_mc():
    c = _cand(id="FMCSA-DOT-9", source="FMCSA", mc=None, dot="9")
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 40
    assert "Registered shipper — DOT 9" in rows[0].reasons


def test_fmcsa_authority_does_not_fire_without_mc_or_dot():
    # A malformed FMCSA row with neither MC nor DOT gets no authority bonus.
    c = _cand(source="FMCSA", mc=None, dot=None)
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0
    assert not any("Registered shipper" in r for r in rows[0].reasons)


def test_osm_source_does_not_earn_fmcsa_authority_even_with_mc():
    # OSM rows never carry FMCSA authority — even if we ever store an mc/dot on them.
    c = _cand(id="OSM-way-1", source="OSM", mc="123", dot=None, osm_tags={})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0


def test_capacity_post_match_adds_25_with_lane_chip():
    c = _cand(source="OSM", mc=None, dot=None, state="NJ", osm_tags={})
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 25
    assert "Fits your NJ→PA truck" in rows[0].reasons


def test_capacity_load_post_matches_dest_state():
    c = _cand(id="OSM-way-2", source="OSM", mc=None, dot=None, state="GA", osm_tags={})
    post = _post(kind="load", origin_state="NJ", dest_state="GA", destinations=[])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 25
    assert "Fits your NJ→GA load" in rows[0].reasons


def test_closed_post_does_not_match():
    c = _cand(source="OSM", mc=None, dot=None, osm_tags={})
    post = _post(status="closed")
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 0
    assert not any("Fits your" in r for r in rows[0].reasons)


def test_phone_adds_15():
    c = _cand(source="OSM", mc=None, dot=None, phone="5551234567", osm_tags={})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 15


def test_email_adds_10():
    c = _cand(source="OSM", mc=None, dot=None, primary_email="ops@acme.example", osm_tags={})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10


def test_osm_named_dc_tag_adds_10():
    c = _cand(
        id="OSM-way-42",
        source="OSM",
        mc=None,
        dot=None,
        osm_tags={"industrial": "distribution_centre"},
    )
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10
    assert "Named DC — distribution_centre" in rows[0].reasons


def test_osm_named_warehouse_tag_adds_10():
    c = _cand(id="OSM-way-43", source="OSM", mc=None, dot=None, osm_tags={"building": "warehouse"})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 10
    assert "Named DC — warehouse" in rows[0].reasons


def test_generic_landuse_industrial_earns_no_bonus():
    # Named or not, `landuse=industrial` doesn't earn a "Named DC" chip — too noisy.
    c = _cand(id="OSM-way-44", source="OSM", mc=None, dot=None, osm_tags={"landuse": "industrial"})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 0
    assert not any("Named DC" in r for r in rows[0].reasons)


def test_already_promoted_gets_5_nudge():
    c = _cand(source="OSM", mc=None, dot=None, promoted_lead_id="MC-99", osm_tags={})
    rows = rank_shippers([c], [], [], TODAY)
    assert rows[0].score == 5


def test_signals_stack_and_cap_at_100():
    # 40 (FMCSA) + 25 (post) + 15 (phone) + 10 (email) + 5 (promoted) = 95 — under 100.
    c = _cand(
        source="FMCSA",
        mc="123",
        state="NJ",
        phone="5551234567",
        primary_email="ops@acme.example",
        promoted_lead_id="MC-1",
    )
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_shippers([c], [post], [], TODAY)
    assert rows[0].score == 95
    # OSM-named-DC on top would push us to 105 — clamp to 100.
    c2 = _cand(
        id="X",
        source="FMCSA",
        mc="123",
        state="NJ",
        phone="5551234567",
        primary_email="ops@acme.example",
        promoted_lead_id="MC-1",
        osm_tags={"building": "warehouse"},
    )
    rows2 = rank_shippers([c2], [post], [], TODAY)
    assert rows2[0].score == 100


# ---- drop rules ------------------------------------------------------------


def test_out_of_region_state_drops():
    c = _cand(state="TX")  # not in the 32-state footprint
    assert rank_shippers([c], [], [], TODAY) == []


def test_empty_name_drops():
    c = _cand(name="")
    assert rank_shippers([c], [], [], TODAY) == []


def test_promoted_lead_ever_not_interested_drops():
    c = _cand(source="FMCSA", mc="123", promoted_lead_id="MC-123")
    ni = _outcome("MC-123", "not_interested", days_ago=400)
    assert rank_shippers([c], [], [ni], TODAY) == []


def test_unpromoted_row_is_not_affected_by_outcomes():
    # No `promoted_lead_id` → the outcomes index can't touch us (nothing to key on).
    c = _cand(source="FMCSA", mc="123", promoted_lead_id=None)
    ni = _outcome("MC-123", "not_interested", days_ago=1)
    rows = rank_shippers([c], [], [ni], TODAY)
    assert len(rows) == 1


# ---- filters ---------------------------------------------------------------


def test_filter_state_narrows_predictably():
    a = _cand(id="A", state="NJ", source="OSM", mc=None, dot=None, osm_tags={})
    b = _cand(id="B", state="PA", source="OSM", mc=None, dot=None, osm_tags={})
    rows = rank_shippers([a, b], [], [], TODAY, filters=ShipperFilters(state="pa"))
    assert [r.id for r in rows] == ["B"]


def test_filter_source_narrows_predictably():
    a = _cand(id="FMCSA-MC-1", source="FMCSA", mc="1")
    b = _cand(id="OSM-way-1", source="OSM", mc=None, dot=None, osm_tags={})
    rows = rank_shippers([a, b], [], [], TODAY, filters=ShipperFilters(source="OSM"))
    assert [r.id for r in rows] == ["OSM-way-1"]


def test_filter_min_score_narrows_predictably():
    hi = _cand(id="HI", source="FMCSA", mc="1")  # score 40
    lo = _cand(id="LO", source="OSM", mc=None, dot=None, osm_tags={})  # score 0
    rows = rank_shippers([hi, lo], [], [], TODAY, filters=ShipperFilters(min_score=10))
    assert [r.id for r in rows] == ["HI"]


def test_filter_promoted_only_true_and_false():
    promoted = _cand(id="P", source="FMCSA", mc="1", promoted_lead_id="MC-1")
    unpromoted = _cand(id="U", source="OSM", mc=None, dot=None, osm_tags={})
    both = [promoted, unpromoted]
    only_p = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=True))
    only_u = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=False))
    everything = rank_shippers(both, [], [], TODAY, filters=ShipperFilters(promoted_only=None))
    assert [r.id for r in only_p] == ["P"]
    assert [r.id for r in only_u] == ["U"]
    assert {r.id for r in everything} == {"P", "U"}


def test_filter_q_case_insensitive_substring():
    a = _cand(id="A", name="Acme Manufacturing", source="OSM", mc=None, dot=None, osm_tags={})
    b = _cand(id="B", name="Zenith Warehousing", source="OSM", mc=None, dot=None, osm_tags={})
    rows = rank_shippers([a, b], [], [], TODAY, filters=ShipperFilters(q="acme"))
    assert [r.id for r in rows] == ["A"]


def test_filters_combine():
    a = _cand(id="A", state="NJ", source="FMCSA", mc="1", name="Acme")  # score 40, state NJ
    b = _cand(id="B", state="PA", source="FMCSA", mc="2", name="Acme")  # score 40, state PA
    c = _cand(id="C", state="NJ", source="OSM", mc=None, dot=None, name="Acme", osm_tags={})  # 0
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
    cands = [_cand(id=f"FMCSA-MC-{i:04d}", mc=str(i)) for i in range(120)]
    rows = rank_shippers(cands, [], [], TODAY, filters=ShipperFilters(limit=50))
    assert len(rows) == 50


def test_limit_is_hard_capped_at_200():
    # Even if the caller asks for 10_000, we never return more than the safety cap.
    cands = [_cand(id=f"FMCSA-MC-{i:04d}", mc=str(i)) for i in range(250)]
    rows = rank_shippers(cands, [], [], TODAY, filters=ShipperFilters(limit=10_000))
    assert len(rows) == SHIPPER_FINDER_LIMIT_CAP


def test_default_limit_is_50():
    cands = [_cand(id=f"FMCSA-MC-{i:04d}", mc=str(i)) for i in range(120)]
    rows = rank_shippers(cands, [], [], TODAY)
    assert len(rows) == 50


def test_deterministic_same_input_same_output():
    cands = [
        _cand(id=f"FMCSA-MC-{i:03d}", mc=str(i), name=f"Shipper {i}", state="NJ" if i % 2 else "PA") for i in range(20)
    ]
    a = rank_shippers(cands, [], [], TODAY)
    b = rank_shippers(cands, [], [], TODAY)
    assert [(r.id, r.score, r.reasons) for r in a] == [(r.id, r.score, r.reasons) for r in b]


def test_deterministic_tiebreak_is_id_ascending():
    # Two rows with equal score → the smaller id wins.
    a = _cand(id="OSM-way-Z", source="OSM", mc=None, dot=None, osm_tags={})
    b = _cand(id="OSM-way-A", source="OSM", mc=None, dot=None, osm_tags={})
    rows = rank_shippers([a, b], [], [], TODAY)
    assert [r.id for r in rows] == ["OSM-way-A", "OSM-way-Z"]


def test_row_carries_all_display_fields():
    c = _cand(
        id="FMCSA-MC-1",
        source="FMCSA",
        mc="1",
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


# ---- model + migration smoke ----------------------------------------------


def test_shipper_candidates_registered_on_metadata():
    """Cheap smoke: the model is imported and the table sits on Base.metadata with
    the columns + indexes migration 0004 creates. Prevents drift between model and
    migration without touching a real DB.
    """
    t = Base.metadata.tables.get("shipper_candidates")
    assert t is not None
    cols = set(t.columns.keys())
    expected_cols = {
        "id",
        "source",
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
    # PK is `id`.
    assert [c.name for c in t.primary_key.columns] == ["id"]
    # NOT NULL invariants.
    assert not t.columns["source"].nullable
    assert not t.columns["name"].nullable
    assert not t.columns["state"].nullable
    # Nullable business columns.
    assert t.columns["promoted_lead_id"].nullable
    assert t.columns["mc"].nullable
    # Indexes present (name only — the migration owns the on-disk shape).
    index_names = {ix.name for ix in t.indexes}
    assert {
        "shipper_candidates_state",
        "shipper_candidates_source",
        "shipper_candidates_unpromoted",
    }.issubset(index_names)
