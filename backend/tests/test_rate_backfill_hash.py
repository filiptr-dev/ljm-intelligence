"""Scoped tests for the dedupe-hash escape hatch (null-lane rows).

Companion to the loads bug fix: inbox rows with null lane used to
collapse into one hash of empty strings. We now hash by
``source|source_ref`` so each unknown-lane row is its own card.
"""

from __future__ import annotations

from app.analysis.rate_backfill import _group_hash


def test_null_lane_rows_get_distinct_hashes() -> None:
    h1 = _group_hash(
        broker_name=None,
        origin_state=None,
        dest_state=None,
        pickup_date=None,
        equipment=None,
        source="inbox",
        source_ref="mailbox-a:msg-1",
    )
    h2 = _group_hash(
        broker_name=None,
        origin_state=None,
        dest_state=None,
        pickup_date=None,
        equipment=None,
        source="inbox",
        source_ref="mailbox-a:msg-2",
    )
    assert h1 != h2


def test_known_lane_rows_still_group_together() -> None:
    """The usual case: two inbox rows for the same lane collapse."""
    kwargs = {
        "broker_name": "Acme Logistics",
        "origin_state": "OH",
        "dest_state": "CT",
        "pickup_date": None,
        "equipment": None,
        "source": "inbox",
    }
    h1 = _group_hash(**kwargs, source_ref="msg-1")
    h2 = _group_hash(**kwargs, source_ref="msg-2")
    assert h1 == h2


def test_hash_is_deterministic() -> None:
    """Same inputs → same hash, so a re-ingest doesn't drift."""
    kwargs = {
        "broker_name": "Acme",
        "origin_state": "OH",
        "dest_state": "CT",
        "pickup_date": None,
        "equipment": "van",
        "source": "inbox",
        "source_ref": "x",
    }
    assert _group_hash(**kwargs) == _group_hash(**kwargs)
