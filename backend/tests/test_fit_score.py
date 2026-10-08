"""Fit-score tests — pure function, no DB, no network.

Cases per scope change 2026-09-30:
  * strong shipper — hits multiple signals, lands high
  * only generic email — penalty pulls it down
  * out-of-region — misses the region weight
  * no data — returns (0, ["not enough data"])
  * weights override — settings blob widens/narrows a signal
"""

from __future__ import annotations

from app.prospecting.scoring import (
    DEFAULT_WEIGHTS,
    SiteSignals,
    build_signals,
    compute_fit,
    extract_signals_from_text,
    is_generic_email,
)


def test_strong_shipper_scores_high():
    text = (
        "Acme is a leading food and beverage manufacturer that ships nationwide. "
        "We operate a distribution center in Newark and a warehouse in Trenton. "
        "Our fleet uses dry van and reefer trailers."
    )
    contacts = [
        {"email": "jane.freight@acme.com", "phone": "5551234567", "title": "Logistics Manager", "is_dm": True},
    ]
    signals = build_signals(state="NJ", contacts=contacts, text=text)
    score, reasons = compute_fit(signals)
    assert score >= 70, (score, reasons)
    assert any("In-region" in r for r in reasons)
    assert any("decision-maker" in r for r in reasons)
    assert any("Warehouse" in r for r in reasons)


def test_generic_only_email_pulls_score_down():
    contacts = [{"email": "info@x.com", "phone": None, "title": None, "is_dm": False}]
    signals = build_signals(state="NJ", contacts=contacts, text="")
    score, reasons = compute_fit(signals)
    # In-region (+15) - penalty (-10) = 5; no other signals.
    assert score < 10
    assert any("generic email" in r for r in reasons)


def test_out_of_region_misses_the_region_weight():
    text = "distribution center. ships nationwide."
    contacts = [{"email": "ops@x.com", "phone": "5551234567", "title": "Warehouse Manager", "is_dm": True}]
    ca_signals = build_signals(state="CA", contacts=contacts, text=text)  # out of region
    nj_signals = build_signals(state="NJ", contacts=contacts, text=text)
    ca_score, _ = compute_fit(ca_signals)
    nj_score, _ = compute_fit(nj_signals)
    assert nj_score - ca_score == DEFAULT_WEIGHTS["in_region"]


def test_low_data_returns_zero_with_reason():
    signals = SiteSignals()  # all defaults, no state
    score, reasons = compute_fit(signals)
    assert score == 0
    assert reasons == ["not enough data"]


def test_weights_override_from_settings():
    text = "ships nationwide"
    contacts: list[dict] = []
    signals = build_signals(state="NJ", contacts=contacts, text=text)
    baseline, _ = compute_fit(signals)
    boosted, _ = compute_fit(signals, weights={**DEFAULT_WEIGHTS, "ships_nationwide": 40})
    assert boosted - baseline == 40 - DEFAULT_WEIGHTS["ships_nationwide"]


def test_generic_email_detection():
    assert is_generic_email("info@x.com") is True
    assert is_generic_email("Hello@X.COM") is True
    assert is_generic_email("jane.freight@x.com") is False
    assert is_generic_email(None) is False


def test_text_signals_extraction():
    s = extract_signals_from_text("We have a distribution center. Reefer + dry van fleet. retail chain.")
    assert s["has_warehouse_or_dc_mention"] is True
    assert s["equipment_dry_van_or_reefer_or_flatbed"] is True
    assert "retail" in s["industry_keywords_hit"]
