"""Settings — unsubscribe config + auto-outreach arm-time guard.

Covers migration 0007 columns, https-only validator on the base URL, the arm-
time 409 refusal, that the raw secret never leaves the API, env-override
precedence, and the send-time `no_unsub_config` refusal + fit-score gating.

All offline: the module conftest pins sqlite in-memory and the test builds a
hermetic Settings so `.env` is never read.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import Lead, LeadContact, SentLog, SettingsRow


@pytest.fixture
async def engine():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _hermetic_settings(**overrides):
    from app.config import Settings

    base = {
        "_env_file": None,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "cron_secret": SecretStr("test-cron-secret"),
        # Deliberately NO unsubscribe_secret in env — we want the DB backfill
        # + effective_unsub precedence to drive most tests.
        "unsubscribe_secret": None,
        "unsubscribe_base_url": None,
        "outreach_postal_address": "22 Troy Lane, Lincoln Park NJ",
    }
    base.update(overrides)
    return Settings(**base)


_CRON_HEADERS = {"X-Cron-Secret": "test-cron-secret"}


@pytest.fixture
async def app(sm):
    a = create_app()
    a.state.sessionmaker = sm
    a.state.settings = _hermetic_settings()
    return a


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c


# ---- schema round-trip -----------------------------------------------------


async def test_migration_0007_columns_present(sm):
    """Selecting the new columns must not raise — proves model + DDL agree."""
    async with sm() as s:
        await s.execute(
            select(
                SettingsRow.unsubscribe_secret,
                SettingsRow.unsubscribe_base_url,
                SettingsRow.auto_outreach_min_fit,
            )
        )


# ---- API surface -----------------------------------------------------------


async def test_settings_get_never_returns_secret_value(sm, client):
    """A curious client must not be able to read the raw HMAC secret. The API
    only ships an advisory boolean. Regression fence."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s3cret-token", unsubscribe_base_url=None))
        await s.commit()
    r = await client.get("/settings")
    body = r.json()
    assert r.status_code == 200
    assert body["unsub_secret_set"] is True
    assert body["unsub_config_ready"] is False  # base URL missing
    # Belt: the value must not appear anywhere in the response body.
    assert "s3cret-token" not in r.text


async def test_settings_put_rejects_non_https_base_url(sm, client):
    r = await client.put("/settings", json={"unsubscribe_base_url": "http://x.example"})
    assert r.status_code == 422


async def test_settings_put_accepts_https_base_url_and_flips_ready(sm, client):
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s", unsubscribe_base_url=None))
        await s.commit()
    r = await client.put("/settings", json={"unsubscribe_base_url": "https://ljm.example"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["unsubscribe_base_url"] == "https://ljm.example"
    assert body["unsub_config_ready"] is True


async def test_settings_put_arm_refuses_409_when_base_url_missing(sm, client):
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s", unsubscribe_base_url=None))
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_enabled": True})
    assert r.status_code == 409
    body = r.json()
    assert body["detail"]["error"] == "unsub_config_missing"
    assert body["detail"]["missing"] == "unsubscribe_base_url"
    # Row unchanged.
    async with sm() as s:
        row = (await s.execute(select(SettingsRow))).scalar_one()
    assert row.auto_outreach_enabled is False


async def test_settings_put_arm_allowed_when_config_ready(sm, client):
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                unsubscribe_secret="s",
                unsubscribe_base_url="https://ljm.example",
            )
        )
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_enabled": True})
    assert r.status_code == 200
    assert r.json()["auto_outreach_enabled"] is True


async def test_settings_put_disarm_never_blocked(sm, client):
    """Even with config missing, turning auto-outreach OFF must succeed."""
    async with sm() as s:
        s.add(SettingsRow(id=1, auto_outreach_enabled=True, unsubscribe_secret=None, unsubscribe_base_url=None))
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_enabled": False})
    assert r.status_code == 200
    assert r.json()["auto_outreach_enabled"] is False


async def test_min_fit_validation(sm, client):
    async with sm() as s:
        s.add(SettingsRow(id=1))
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_min_fit": 101})
    assert r.status_code == 422
    r = await client.put("/settings", json={"auto_outreach_min_fit": -1})
    assert r.status_code == 422
    r = await client.put("/settings", json={"auto_outreach_min_fit": 40})
    assert r.status_code == 200
    assert r.json()["auto_outreach_min_fit"] == 40


# ---- effective_unsub precedence -------------------------------------------


async def test_env_override_wins_over_db(sm, client, app):
    """DB has base URL blank; env sets it → config resolves ready."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s", unsubscribe_base_url=None))
        await s.commit()
    app.state.settings = _hermetic_settings(
        unsubscribe_base_url="https://env.example",
        unsubscribe_secret=SecretStr("env-secret"),
    )
    r = await client.get("/settings")
    body = r.json()
    assert body["unsub_config_ready"] is True
    # DB echo unchanged (env is invisible to the form).
    assert body["unsubscribe_base_url"] is None


# ---- send-time guard + fit filter -----------------------------------------


async def _seed_two_leads_with_contacts(sm, *, fits: dict[str, int | None]):
    async with sm() as s:
        for lid, fs in fits.items():
            s.add(
                Lead(
                    id=lid,
                    name=lid,
                    kind="Shipper",
                    state="NJ",
                    raw={},
                    evidence={},
                    recommendations=[],
                    fit_score=fs,
                )
            )
        await s.flush()
        for lid in fits:
            s.add(
                LeadContact(
                    lead_id=lid,
                    email=f"{lid.lower()}@x.com",
                    pipeline_status="found",
                    is_decision_maker=True,
                )
            )
        await s.commit()


async def test_auto_send_refuses_when_unsub_missing(sm, client, app):
    """After arming, blank base URL → route returns `no_unsub_config`, zero writes."""
    app.state.settings = _hermetic_settings()  # both env values None
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-x",
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,
                unsubscribe_secret="s",  # secret present
                unsubscribe_base_url=None,  # base URL blanked
            )
        )
        await s.commit()
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "no_unsub_config"
    assert body["sent"] == 0
    async with sm() as s:
        logs = (await s.execute(select(SentLog))).scalars().all()
    assert logs == []


async def test_auto_send_filters_by_min_fit_and_orders_desc(sm, client, app):
    """Only leads with fit_score >= threshold get contacted; highest first,
    unscored dropped."""
    app.state.settings = _hermetic_settings(
        unsubscribe_base_url="https://ljm.example",
        unsubscribe_secret=SecretStr("s"),
    )
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-x",
                auto_outreach_daily_cap=10,
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,
                auto_outreach_min_fit=60,
                unsubscribe_secret="s",
                unsubscribe_base_url="https://ljm.example",
            )
        )
        await s.commit()
    await _seed_two_leads_with_contacts(sm, fits={"MC-A": 80, "MC-B": 40, "MC-C": None, "MC-D": 60})

    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "ok", body
    # MC-A (80) and MC-D (60) qualify; MC-B (40) filtered; MC-C (None) filtered.
    assert body["sent"] == 2
    # Fit DESC ordering: MC-A logged before MC-D.
    async with sm() as s:
        logs = (await s.execute(select(SentLog).order_by(SentLog.id))).scalars().all()
    assert [log_.to_email for log_ in logs] == ["mc-a@x.com", "mc-d@x.com"]


async def test_auto_send_unscored_only_returns_ok_zero(sm, client, app):
    app.state.settings = _hermetic_settings(
        unsubscribe_base_url="https://ljm.example",
        unsubscribe_secret=SecretStr("s"),
    )
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-x",
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,
                auto_outreach_min_fit=60,
                unsubscribe_secret="s",
                unsubscribe_base_url="https://ljm.example",
            )
        )
        await s.commit()
    await _seed_two_leads_with_contacts(sm, fits={"MC-X": None, "MC-Y": None})
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    body = r.json()
    assert body["status"] == "ok"
    assert body["sent"] == 0
