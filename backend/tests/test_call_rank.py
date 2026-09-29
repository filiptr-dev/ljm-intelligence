"""Deterministic Call-List ranker — table-driven, no Gemini, no DB.

Mirrors the style of test_match_scorer.py: build ORM objects in memory, call the pure
function, assert on the shape of the output.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from app.models import CallOutcome, CapacityPost, Lead
from app.pipeline.call_rank import CALLBACK_DEFAULT_OFFSET_DAYS, rank_call_list

TODAY = date(2026, 9, 29)


# ---- factories -------------------------------------------------------------


def _lead(**kw) -> Lead:
    defaults = {
        "id": "MC-1",
        "mc": "1",
        "dot": None,
        "name": "Acme Broker LLC",
        "kind": "Broker",
        "state": "NJ",
        "city": "Newark",
        "phone": "5551234567",
        "primary_email": "hi@acme.example",
        "current_score": 50,
        "raw": {},
    }
    defaults.update(kw)
    return Lead(**defaults)


def _with_add_date(lead: Lead, add_dt: date) -> Lead:
    lead.raw = {"fmcsa": {"add_date": add_dt.strftime("%Y%m%d")}}
    return lead


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


def _outcome(lead_id: str, outcome: str, *, days_ago: int = 0, callback_at: date | None = None) -> CallOutcome:
    logged = datetime.combine(TODAY - timedelta(days=days_ago), datetime.min.time(), tzinfo=UTC)
    return CallOutcome(
        lead_id=lead_id,
        outcome=outcome,
        callback_at=callback_at,
        logged_at=logged,
    )


# ---- signals ---------------------------------------------------------------


def test_new_authority_signal_fires_within_30d():
    lead = _with_add_date(_lead(current_score=0, primary_email=None), TODAY - timedelta(days=12))
    rows = rank_call_list([lead], [], [], TODAY)
    assert len(rows) == 1
    assert rows[0].score == 40
    assert any("New MC · 12d" == r for r in rows[0].reasons)


def test_new_authority_signal_does_not_fire_after_30d():
    lead = _with_add_date(_lead(current_score=0, primary_email=None), TODAY - timedelta(days=60))
    rows = rank_call_list([lead], [], [], TODAY)
    assert rows[0].score == 0
    assert not any("New MC" in r for r in rows[0].reasons)


def test_hot_ai_score_scaled_by_0_4():
    lead = _lead(current_score=100, primary_email=None, raw={})
    rows = rank_call_list([lead], [], [], TODAY)
    # 100 * 0.4 = 40; no other signals; no email → +0.
    assert rows[0].score == 40
    assert any("AI score 100" in r for r in rows[0].reasons)


def test_capacity_post_match_adds_20_with_lane_chip():
    lead = _lead(state="NJ", current_score=0, primary_email=None, raw={})
    post = _post(origin_state="NJ", destinations=["PA"])
    rows = rank_call_list([lead], [], [post], TODAY)
    assert rows[0].score == 20
    assert any("Fits your NJ→PA truck" == r for r in rows[0].reasons)


def test_capacity_post_load_kind_matches_dest_state():
    lead = _lead(state="GA", current_score=0, primary_email=None, raw={})
    post = _post(kind="load", origin_state="NJ", dest_state="GA", destinations=[])
    rows = rank_call_list([lead], [], [post], TODAY)
    assert rows[0].score == 20
    assert any("Fits your NJ→GA load" == r for r in rows[0].reasons)


def test_stale_relationship_from_prior_outcome():
    lead = _lead(current_score=0, primary_email=None, raw={})
    prior = _outcome("MC-1", "no_answer", days_ago=47)
    rows = rank_call_list([lead], [prior], [], TODAY)
    assert rows[0].score == 15
    assert any("Last touch 47d ago" == r for r in rows[0].reasons)


def test_stale_relationship_also_reads_email_history():
    lead = _lead(current_score=0, primary_email=None, raw={})
    rows = rank_call_list(
        [lead], [], [], TODAY, last_email_by_lead={"MC-1": TODAY - timedelta(days=40)}
    )
    assert rows[0].score == 15
    assert any("Last touch 40d ago" == r for r in rows[0].reasons)


def test_stale_signal_does_not_fire_outside_30_90_window():
    lead = _lead(current_score=0, primary_email=None, raw={})
    fresh = _outcome("MC-1", "no_answer", days_ago=5)
    rows = rank_call_list([lead], [fresh], [], TODAY)
    # 5d ago no_answer is not "today", so the drop rule for na_today doesn't apply,
    # but it's outside the 30..90 window → no stale chip.
    assert rows[0].score == 0
    assert not any("Last touch" in r for r in rows[0].reasons)


def test_email_present_adds_5():
    a = _lead(id="MC-A", current_score=0, primary_email="a@x.co", raw={})
    b = _lead(id="MC-B", current_score=0, primary_email=None, raw={})
    rows = rank_call_list([a, b], [], [], TODAY)
    by_id = {r.lead_id: r for r in rows}
    assert by_id["MC-A"].score == 5
    assert by_id["MC-B"].score == 0


# ---- suppression rules -----------------------------------------------------


def test_no_phone_drops_the_row():
    lead = _lead(phone=None)
    assert rank_call_list([lead], [], [], TODAY) == []


def test_ever_not_interested_drops_forever():
    lead = _lead()
    old = _outcome("MC-1", "not_interested", days_ago=400)
    assert rank_call_list([lead], [old], [], TODAY) == []


def test_booked_within_14d_drops():
    lead = _lead()
    rows = rank_call_list([lead], [_outcome("MC-1", "booked", days_ago=10)], [], TODAY)
    assert rows == []


def test_booked_more_than_14d_ago_does_not_drop():
    lead = _lead()
    rows = rank_call_list([lead], [_outcome("MC-1", "booked", days_ago=20)], [], TODAY)
    assert len(rows) == 1


def test_no_answer_today_drops():
    lead = _lead()
    rows = rank_call_list([lead], [_outcome("MC-1", "no_answer", days_ago=0)], [], TODAY)
    assert rows == []


def test_scheduled_callback_in_future_drops():
    lead = _lead()
    cb = _outcome("MC-1", "callback", days_ago=1, callback_at=TODAY + timedelta(days=3))
    assert rank_call_list([lead], [cb], [], TODAY) == []


def test_one_call_per_day_drops_after_any_outcome_today():
    # A booked-today outcome must still hide the row for the rest of today.
    lead = _lead()
    rows = rank_call_list([lead], [_outcome("MC-1", "booked", days_ago=0)], [], TODAY)
    assert rows == []


# ---- callback pin ----------------------------------------------------------


def test_callback_pin_bonus_fires_today():
    lead = _lead(current_score=0, primary_email=None, raw={})
    # Callback was scheduled a week ago for today.
    cb = _outcome("MC-1", "callback", days_ago=7, callback_at=TODAY)
    rows = rank_call_list([lead], [cb], [], TODAY)
    assert len(rows) == 1
    assert rows[0].score == 50
    assert "Callback due today" in rows[0].reasons


def test_callback_pin_floats_to_top():
    hot = _lead(id="MC-HOT", current_score=100, primary_email="a@x.co")  # 40+5 = 45
    pinned = _lead(id="MC-PIN", current_score=0, primary_email=None, raw={})
    cb = _outcome("MC-PIN", "callback", days_ago=5, callback_at=TODAY)
    rows = rank_call_list([hot, pinned], [cb], [], TODAY)
    assert rows[0].lead_id == "MC-PIN"
    assert rows[0].score >= rows[1].score


# ---- determinism, opener, top_n, phone-required, callback default ----------


def test_deterministic_same_input_same_output():
    leads = [
        _lead(id=f"MC-{i:03d}", current_score=(i * 3) % 100, name=f"Broker {i}")
        for i in range(10)
    ]
    a = rank_call_list(leads, [], [], TODAY)
    b = rank_call_list(leads, [], [], TODAY)
    assert [(r.lead_id, r.score, r.reasons, r.opener) for r in a] == [
        (r.lead_id, r.score, r.reasons, r.opener) for r in b
    ]


def test_deterministic_tiebreak_is_lead_id_ascending():
    a = _lead(id="MC-Z", current_score=10, primary_email=None, raw={})
    b = _lead(id="MC-A", current_score=10, primary_email=None, raw={})
    rows = rank_call_list([a, b], [], [], TODAY)
    assert [r.lead_id for r in rows] == ["MC-A", "MC-Z"]


def test_top_n_respected():
    leads = [_lead(id=f"MC-{i:03d}", current_score=50) for i in range(40)]
    rows = rank_call_list(leads, [], [], TODAY, top_n=25)
    assert len(rows) == 25


def test_opener_is_non_empty_and_server_rendered():
    lead = _with_add_date(_lead(), TODAY - timedelta(days=5))
    rows = rank_call_list([lead], [], [], TODAY)
    assert rows[0].opener
    assert "Acme Broker LLC" in rows[0].opener
    assert "MC 1" in rows[0].opener


def test_row_carries_phone_and_email():
    lead = _lead(phone="5559998888", primary_email="a@b.co")
    rows = rank_call_list([lead], [], [], TODAY)
    assert rows[0].phone == "5559998888"
    assert rows[0].primary_email == "a@b.co"


def test_callback_default_offset_is_two_days():
    # Slice 3 reads this constant; lock it in so the ranker + the UI never disagree.
    assert CALLBACK_DEFAULT_OFFSET_DAYS == 2
