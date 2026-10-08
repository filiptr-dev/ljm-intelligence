"""Service-layer unit tests — the DIP teaching artifact (plan §I).

Each test here constructs a service dependency with an **in-memory fake**
instead of a real `AsyncSession`, calls the service, and asserts the
result. The point is not coverage — it is a public proof that the
service layer no longer bakes `AsyncSession` into its signature. If
these tests needed to spin up a DB, the plan's DIP goal is not met.

The ONE service that already fits this shape today is the response-
time stat path on `app.inbox.service`: `response_time_stats` takes an
`AsyncSession` by name but only ever hands it to the repository call
`fetch_response_time_rows`, so swapping the repo call for an injected
fake-and-no-session is one monkeypatch away.

For brevity we test one representative per DB-touching module —
analysis, inbox, prospecting, outreach — all through the same recipe:
patch the repo function; call the service; assert the shape.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from unittest.mock import patch

import pytest


# ---- inbox: response_time_stats -----------------------------------------


@pytest.mark.asyncio
async def test_inbox_response_time_stats_no_db():
    """`inbox.service.response_time_stats` uses only repo rows + Python math."""
    from app.inbox import service as svc

    @dataclass
    class _Row:
        thread_id: str
        from_addr: str
        mailbox: str
        sent_at: datetime
        email_lower: str

    base = datetime(2026, 10, 7, 10, 0, 0, tzinfo=UTC)
    fake_rows = [
        _Row("t1", "broker@x.com", "us@ljm.com", base, "broker@x.com"),  # in
        _Row("t1", "us@ljm.com", "us@ljm.com", base.replace(minute=10), "us@ljm.com"),  # out → ours gap=10
        _Row("t1", "broker@x.com", "us@ljm.com", base.replace(minute=30), "broker@x.com"),  # in → theirs gap=20
    ]

    async def _fake_fetch(session, *, broker_domain):
        return fake_rows

    with patch("app.inbox.service.fetch_response_time_rows", _fake_fetch):
        out = await svc.response_time_stats(session=None, broker_domain=None)  # type: ignore[arg-type]

    assert out.ours_count == 1
    assert out.theirs_count == 1
    assert out.ours_median_minutes == 10
    assert out.theirs_median_minutes == 20


# ---- analysis: list_broker_predictions ----------------------------------


@pytest.mark.asyncio
async def test_analysis_list_broker_predictions_no_db():
    from app.analysis import service as svc

    @dataclass
    class _Pred:
        broker_domain: str = "acme.com"
        broker_name: str | None = "Acme"
        win_probability: float = 0.42
        health_score: int = 72
        best_send_hour: int | None = 9
        is_slow_payer: bool = False
        churn_risk: float = 0.1
        reply_speed_lift: float = 1.4
        first_touch_latency_days: float | None = 0.5
        computed_at: datetime = datetime(2026, 10, 8, tzinfo=UTC)

    async def _fake(session, *, broker_domain, limit):
        return [_Pred()]

    with patch("app.analysis.service.list_broker_predictions_rows", _fake):
        rows = await svc.list_broker_predictions(session=None, broker_domain=None, limit=10)  # type: ignore[arg-type]

    assert len(rows) == 1
    assert rows[0].broker_domain == "acme.com"
    assert rows[0].health_score == 72


# ---- prospecting: show_lead ---------------------------------------------


@pytest.mark.asyncio
async def test_prospecting_show_lead_no_db():
    from app.prospecting import service as svc

    @dataclass
    class _Lead:
        id: str = "01lead"
        kind: str = "broker"
        name: str = "Acme Logistics"
        state: str = "GA"
        city: str | None = "Atlanta"
        mc: str | None = "123456"
        dot: str | None = "DOT1"
        domain: str | None = "acme.com"
        primary_email: str | None = "ops@acme.com"
        phone: str | None = None
        current_score: int | None = 80
        first_seen_at: datetime | None = datetime(2026, 10, 1, tzinfo=UTC)
        last_seen_at: datetime | None = datetime(2026, 10, 7, tzinfo=UTC)
        address: str | None = "123 Peach"
        raw: dict | None = None
        evidence: dict | None = None

    async def _get(session, lead_id):
        return _Lead()

    async def _srcs(session, lead_id):
        return ["fmcsa", "web"]

    async def _contacts(session, lead_id):
        return []

    async def _score(session, lead_id):
        return None

    with (
        patch("app.prospecting.service.get_lead", _get),
        patch("app.prospecting.service.list_sources_for_lead", _srcs),
        patch("app.prospecting.service.list_contacts_for_lead", _contacts),
        patch("app.prospecting.service.latest_score_for_lead", _score),
    ):
        row = await svc.show_lead(session=None, lead_id="01lead")  # type: ignore[arg-type]

    assert row is not None
    assert row.id == "01lead"
    assert row.sources == ["fmcsa", "web"]


# ---- outreach: auto_send dry-run disabled path --------------------------


@pytest.mark.asyncio
async def test_outreach_auto_send_disabled_no_db():
    """`disabled` branch exercises the repo seam without needing candidates."""
    from app.outreach import service as svc

    @dataclass
    class _Cfg:
        auto_outreach_enabled: bool = False
        auto_outreach_template_id: int | None = None

    async def _get_settings(s):
        return _Cfg()

    class _Noop:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def _sessionmaker():
        return _Noop()

    with patch("app.outreach.service.get_settings_row", _get_settings):
        result = await svc.auto_send(
            sessionmaker=_sessionmaker,
            settings=type("S", (), {"outreach_postal_address": "123"})(),
            dry_run=True,
        )

    assert result.status == "disabled"
    assert result.sent == 0
