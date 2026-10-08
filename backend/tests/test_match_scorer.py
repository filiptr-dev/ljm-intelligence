"""Deterministic broker-match scorer — no Gemini, pure function."""

from __future__ import annotations

from datetime import UTC, datetime

from app.models import CapacityPost, Lead
from app.prospecting.pipeline.match import score_broker_for_post


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


def _lead(**kw) -> Lead:
    defaults = {
        "id": "MC-1",
        "name": "Test Broker LLC",
        "kind": "Broker",
        "state": "NJ",
        "current_score": 80,
        "primary_email": "hi@example.com",
        "phone": "5551234567",
        "first_seen_at": datetime.now(UTC),
    }
    defaults.update(kw)
    return Lead(**defaults)


def test_same_origin_state_scores_high():
    r = score_broker_for_post(_post(), _lead(state="NJ"))
    assert r.score >= 70
    assert "operates NJ" in r.reason


def test_out_of_region_still_scores_by_ai_and_freshness():
    r = score_broker_for_post(_post(), _lead(state="TX", current_score=60))
    # No origin match, no dest match, but AI score + new authority + contact fitness
    assert 0 <= r.score <= 100
    assert "operates NJ" not in r.reason


def test_truck_post_destination_state_bonus():
    r = score_broker_for_post(_post(destinations=["GA"]), _lead(state="GA", current_score=0))
    # +25 for buying into GA (truck goes there), +5 freshness, +5 contact fitness
    assert r.score >= 30
    assert "buys into GA" in r.reason


def test_load_post_destination_state_bonus():
    r = score_broker_for_post(_post(kind="load", dest_state="GA"), _lead(state="GA", current_score=0))
    assert "drops in GA" in r.reason
