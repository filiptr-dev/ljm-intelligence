"""Parse LoadStop ``locationDescription`` → approximate pin.

Scope: the parser only. No DB, no network.
"""
from __future__ import annotations

from app.integrations.adapters.loadboard.loadstop.ingest.geocode import (
    STATE_CENTROIDS,
    geocode,
    parse,
)


def test_plain_city_state() -> None:
    p = parse("Hartford, CT (moving)")
    assert p is not None
    assert p.city == "Hartford"
    assert p.state == "CT"
    assert (p.lat, p.lng) == STATE_CENTROIDS["CT"]
    assert "CT" in p.label


def test_mile_prefix() -> None:
    p = parse("5.9 mi NE of Burton, NC (moving)")
    assert p is not None
    assert p.state == "NC"
    assert p.city == "Burton"


def test_township() -> None:
    p = parse("1.7 mi E of Hamilton Township, NJ (moving)")
    assert p is not None
    assert p.state == "NJ"
    assert p.city == "Hamilton Township"


def test_engine_stop_status() -> None:
    p = parse("Butler, NJ (engine stop)")
    assert p is not None
    assert p.state == "NJ"


def test_no_status_parenthetical() -> None:
    p = parse("Butler, NJ")
    assert p is not None
    assert p.state == "NJ"


def test_unparseable_returns_none() -> None:
    assert parse("") is None
    assert parse(None) is None
    assert parse("random text with no state") is None


def test_unknown_state_returns_none() -> None:
    # XX is not a state → fail safely (ingest keeps row null)
    assert parse("Nowhere, XX (moving)") is None


def test_geocode_shorthand() -> None:
    assert geocode("Hartford, CT") == STATE_CENTROIDS["CT"]
    assert geocode("") is None


def test_all_states_have_valid_centroids() -> None:
    # Guards against a bad hand-edit of the table.
    assert len(STATE_CENTROIDS) == 51  # 50 states + DC
    for st, (lat, lng) in STATE_CENTROIDS.items():
        assert len(st) == 2
        assert 18.0 <= lat <= 72.0, (st, lat)  # covers HI south / AK north
        assert -180.0 <= lng <= -66.0, (st, lng)
