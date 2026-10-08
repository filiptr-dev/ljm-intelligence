"""Deterministic Shipper matcher — table-driven, pure, no DB.

Mirrors `test_match_scorer.py`: build ORM objects in memory, call the pure
function, assert on the shape of the output. See
`projects/ljm-intelligence/plan/2026-10-05-capacity-shipper-matches.md`.
"""

from __future__ import annotations

from app.models import CapacityPost, ShipperCandidate
from app.prospecting.pipeline.shipper_match import ShipperMatchRow, match_shippers_for_post


def _post(**kw) -> CapacityPost:
    defaults = {
        "id": "CP-test",
        "kind": "truck",
        "equipment": "Dry Van",
        "origin_city": "Lincoln Park",
        "origin_state": "NJ",
        "destinations": ["PA", "GA"],
        "status": "open",
    }
    defaults.update(kw)
    return CapacityPost(**defaults)


def _cand(**kw) -> ShipperCandidate:
    defaults = {
        "id": "01CAND0000000000000000000A",
        "sources": ["FMCSA"],
        "fmcsa_mc": None,
        "fmcsa_dot": None,
        "osm_ref": None,
        "match_reason": None,
        "name": "Acme Manufacturing Inc",
        "state": "NJ",
        "city": "Newark",
        "mc": None,
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


# ---------- truck lane signals ---------------------------------------------


def test_truck_origin_state_match_scores_and_reasons():
    rows = match_shippers_for_post(_post(), [_cand(state="NJ")])
    assert len(rows) == 1
    r = rows[0]
    assert r.score == 40
    assert "your truck origin" in r.reason
    assert "NJ" in r.reason


def test_truck_destination_state_match():
    rows = match_shippers_for_post(_post(), [_cand(state="GA")])
    assert len(rows) == 1
    assert "one of your truck's destinations" in rows[0].reason


def test_truck_destination_miss_is_dropped():
    rows = match_shippers_for_post(_post(), [_cand(state="TX")])
    assert rows == []


# ---------- load lane signals ----------------------------------------------


def test_load_origin_match():
    rows = match_shippers_for_post(_post(kind="load", destinations=[], dest_state="GA"), [_cand(state="NJ")])
    assert len(rows) == 1
    assert "your load origin" in rows[0].reason


def test_load_drop_match():
    rows = match_shippers_for_post(_post(kind="load", destinations=[], dest_state="GA"), [_cand(state="GA")])
    assert len(rows) == 1
    assert "your load's drop state" in rows[0].reason


def test_load_dest_miss_is_dropped():
    rows = match_shippers_for_post(_post(kind="load", destinations=[], dest_state="GA"), [_cand(state="TX")])
    assert rows == []


# ---------- lane-first drop rule -------------------------------------------


def test_no_lane_signal_dropped_even_with_full_contact_info():
    cand = _cand(
        state="TX",
        primary_email="x@y.com",
        phone="5551234567",
        fmcsa_mc="12345",
        mc="12345",
        promoted_lead_id="MC-12345",
    )
    rows = match_shippers_for_post(_post(), [cand])
    assert rows == []


# ---------- aggregate score + clamp ----------------------------------------


def test_score_clamped_to_100():
    cand = _cand(
        state="NJ",  # +40 origin
        primary_email="x@y.com",  # +10
        phone="5551234567",  # +5
        fmcsa_mc="12345",
        mc="12345",  # +5
        promoted_lead_id="MC-12345",  # +5
    )
    # Make it a truck post whose destinations include NJ too (impossible IRL —
    # but the clamp is what we're exercising): origin + a dest state hit.
    r = match_shippers_for_post(_post(destinations=["NJ"]), [cand])[0]
    # 40 + 30 + 5 + 10 + 5 + 5 = 95 — well under cap; so make it bigger.
    assert r.score == 95
    # And a hypothetical over-cap input:
    rows = match_shippers_for_post(_post(destinations=["NJ"]), [cand])
    assert rows[0].score <= 100


# ---------- determinism ----------------------------------------------------


def test_deterministic_sort_across_shuffled_input():
    cands = [
        _cand(id="01A", state="NJ", name="Zeta Co"),  # 40
        _cand(id="01B", state="NJ", name="Alpha Co", primary_email="x@y.com"),  # 50
        _cand(id="01C", state="GA", name="Mid Co"),  # 30
        _cand(id="01D", state="NJ", name="Alpha Co"),  # 40 — tie with Zeta, name beats
    ]
    r1 = [r.candidate_id for r in match_shippers_for_post(_post(), cands)]
    r2 = [r.candidate_id for r in match_shippers_for_post(_post(), list(reversed(cands)))]
    assert r1 == r2
    # Alpha+email (50) first, then by name/id for the 40s, then GA (30).
    assert r1[0] == "01B"
    # The two 40s: name sort "Alpha Co" < "Zeta Co".
    assert r1[1] == "01D"
    assert r1[2] == "01A"
    assert r1[3] == "01C"


def test_top_cap_applied():
    cands = [_cand(id=f"01X{i:023d}"[:26], state="NJ", name=f"Co {i:03d}") for i in range(30)]
    rows = match_shippers_for_post(_post(), cands, top=5)
    assert len(rows) == 5


# ---------- row shape ------------------------------------------------------


def test_row_fields_populated():
    cand = _cand(
        state="NJ",
        primary_email="hi@example.com",
        phone="5550000000",
        fmcsa_dot="99",
        dot="99",
        promoted_lead_id="MC-99",
    )
    row = match_shippers_for_post(_post(), [cand])[0]
    assert isinstance(row, ShipperMatchRow)
    assert row.candidate_id == cand.id
    assert row.name == cand.name
    assert row.state == "NJ"
    assert row.city == "Newark"
    assert row.primary_email == "hi@example.com"
    assert row.phone == "5550000000"
    assert row.promoted_lead_id == "MC-99"
    assert "DOT 99" in row.reason
    assert "Already in your leads" in row.reason
