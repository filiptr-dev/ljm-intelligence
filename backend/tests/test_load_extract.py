"""Scoped unit tests for :mod:`app.inbox.load_extract`.

Covers the four real-shape fixtures from the user bug report, the
quoted-reply boundary, the plausible-rate guardrail, and the empty-case.
Keeps the surface pure: no DB, no provider.
"""

from __future__ import annotations

from decimal import Decimal

from app.inbox.load_extract import (
    EXTRACTOR_VERSION,
    LOAD_INTENTS,
    extract_from_message,
)


def test_extract_lane_and_order_from_ntg_subject() -> None:
    ex = extract_from_message(
        "NTG Rate confirmation: Load # 9737804 Cleveland, OH to North Haven, CT",
        None,
    )
    assert ex.has_lane
    assert ex.origin_city == "Cleveland"
    assert ex.origin_state == "OH"
    assert ex.dest_city == "North Haven"
    assert ex.dest_state == "CT"
    assert ex.order_no == "9737804"
    assert ex.source == EXTRACTOR_VERSION


def test_extract_lane_from_dash_separator_subject() -> None:
    ex = extract_from_message(
        "Re: Durham, NC - Kersey, PA // #31405-27609 - Rate Confirmation",
        None,
    )
    assert ex.has_lane
    assert ex.origin_state == "NC"
    assert ex.dest_state == "PA"


def test_extract_order_only_no_lane() -> None:
    """EPES-style: order shows up, lane does not."""
    ex = extract_from_message(
        "EPES LOGISTICS SERVICES, INC. Rate Confirmation for order: 2403768",
        None,
    )
    assert not ex.has_lane
    assert ex.order_no == "2403768"


def test_extract_tender_order_only() -> None:
    ex = extract_from_message("Load Tender for 759605", None)
    assert not ex.has_lane
    assert ex.order_no == "759605"


def test_extract_rate_from_body_labeled() -> None:
    ex = extract_from_message(
        "Rate for your truck",
        "All-in rate: $1,700. Pickup date: 2026-10-20. Reefer needed.",
    )
    assert ex.rate_usd == Decimal(1700)
    assert ex.equipment == "reefer"
    assert ex.pickup_date is not None
    assert ex.pickup_date.year == 2026


def test_extract_rejects_implausible_rate() -> None:
    """A '$5' in a signature isn't a load rate."""
    ex = extract_from_message("Thanks!", "Call me back — $5 is my coffee.")
    assert ex.rate_usd is None


def test_extract_strips_quoted_reply() -> None:
    """Lane in the quoted history must not drive extraction."""
    body = (
        "No lane here.\n"
        "\n"
        "On Mon, Oct 13, 2026, Alice <alice@acme.com> wrote:\n"
        "> Rate: $2,400. Chicago, IL to Dallas, TX.\n"
    )
    ex = extract_from_message("Re: your reply", body)
    # The quoted reply must not leak through.
    assert not ex.has_lane
    assert ex.rate_usd is None


def test_extract_empty_subject_and_body_is_empty() -> None:
    ex = extract_from_message(None, None)
    assert ex.is_empty


def test_load_intents_cover_the_rate_relevant_set() -> None:
    assert "booked" in LOAD_INTENTS
    assert "load_offer" in LOAD_INTENTS
    assert "rate_request" in LOAD_INTENTS
    assert "routine" not in LOAD_INTENTS
