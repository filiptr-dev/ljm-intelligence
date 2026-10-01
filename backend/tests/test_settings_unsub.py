"""Settings — unsubscribe config + auto-outreach arm-time guard.

Covers migration 0007 columns, that the unsubscribe base URL is NOT part of
the settings API, the arm-time 409 refusal, that the raw secret never leaves
the API, the always-on unsubscribe footer (built from the fixed public base,
never the request host), the send-time `no_unsub_config` refusal, and
fit-score gating.

All offline: the module conftest pins sqlite in-memory and the test builds a
hermetic Settings so `.env` is never read.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import EmailTemplate, Lead, LeadContact, SentLog, SettingsRow


async def _seed_tmpl_x(sm) -> None:
    """Seed the `tmpl-x` row referenced by `settings.auto_outreach_template_id`.

    PG16 enforces the FK; sqlite doesn't. Keeping the seed here (instead of
    weakening the migration) keeps the test's intent explicit and future-proofs
    against the strict harness.
    """
    async with sm() as s:
        s.add(
            EmailTemplate(
                id="tmpl-x",
                name="tmpl-x",
                subject="Hi",
                body="Hello {name} — {{unsub}}",
                tokens=[],
            )
        )
        await s.commit()


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
    # Base URL is the fixed code default, so a seeded secret alone is ready.
    assert body["unsub_config_ready"] is True
    # Belt: the value must not appear anywhere in the response body.
    assert "s3cret-token" not in r.text


async def test_settings_api_does_not_expose_or_accept_base_url(sm, client):
    """The unsubscribe base URL is not configurable: it is neither returned by
    GET nor persisted by PUT (unknown field is ignored)."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s", unsubscribe_base_url=None))
        await s.commit()
    r = await client.get("/settings")
    assert "unsubscribe_base_url" not in r.json()
    r = await client.put("/settings", json={"unsubscribe_base_url": "https://evil.example"})
    assert r.status_code == 200, r.text
    assert "unsubscribe_base_url" not in r.json()
    async with sm() as s:
        row = (await s.execute(select(SettingsRow))).scalar_one()
    assert row.unsubscribe_base_url is None


async def test_settings_put_arm_allowed_with_secret_only(sm, client):
    """With the fixed base URL in config, a secret is all arming needs."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="s", unsubscribe_base_url=None))
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_enabled": True})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auto_outreach_enabled"] is True
    assert body["unsub_config_ready"] is True


async def test_settings_put_arm_still_refuses_when_secret_missing(sm, client):
    """No HMAC secret anywhere → the link can't be built → 409 on arm."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret=None, unsubscribe_base_url=None))
        await s.commit()
    r = await client.put("/settings", json={"auto_outreach_enabled": True})
    assert r.status_code == 409
    assert r.json()["detail"]["error"] == "unsub_config_missing"
    assert r.json()["detail"]["missing"] == "unsubscribe_secret"


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


async def test_env_secret_wins_over_db(sm, client, app):
    """Env secret alone (DB secret blank) → config resolves ready."""
    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret=None, unsubscribe_base_url=None))
        await s.commit()
    app.state.settings = _hermetic_settings(unsubscribe_secret=SecretStr("env-secret"))
    r = await client.get("/settings")
    assert r.json()["unsub_config_ready"] is True


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


async def test_auto_send_refuses_when_secret_missing(sm, client, app):
    """No HMAC secret → the unsubscribe link can't be built → refuse with
    `no_unsub_config`: zero sends, zero rows."""
    app.state.settings = _hermetic_settings()  # both env values None
    await _seed_tmpl_x(sm)
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-x",
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,
                unsubscribe_secret=None,  # secret missing → no HMAC available
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
    app.state.settings = _hermetic_settings(unsubscribe_secret=SecretStr("s"))
    await _seed_tmpl_x(sm)
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
    app.state.settings = _hermetic_settings(unsubscribe_secret=SecretStr("s"))
    await _seed_tmpl_x(sm)
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
            )
        )
        await s.commit()
    await _seed_two_leads_with_contacts(sm, fits={"MC-X": None, "MC-Y": None})
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    body = r.json()
    assert body["status"] == "ok"
    assert body["sent"] == 0


# ---- always-on unsubscribe footer -----------------------------------------

_DEFAULT_BASE = "https://ljm-intelligence-api.onrender.com"


async def _arm_with_one_contact(sm, *, secret: str | None = "db-secret"):
    await _seed_tmpl_x(sm)
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-x",
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,
                unsubscribe_secret=secret,
                # Legacy column set to a bogus value: it must be ignored.
                unsubscribe_base_url="https://legacy-db.example",
            )
        )
        await s.commit()
    await _seed_two_leads_with_contacts(sm, fits={"MC-F": 90})


def test_code_default_base_url_is_public_render_origin():
    assert _hermetic_settings().unsubscribe_base_url == _DEFAULT_BASE


async def test_every_sent_email_has_footer_from_fixed_base_never_request_host(sm, client):
    """Footer is always appended, and the link uses the configured public base
    — never the request host (`http://t` here) nor the legacy DB column."""
    await _arm_with_one_contact(sm)
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    assert r.json()["status"] == "ok", r.json()
    async with sm() as s:
        logs = (await s.execute(select(SentLog))).scalars().all()
    assert len(logs) == 1
    body = logs[0].body
    assert f"Unsubscribe: {_DEFAULT_BASE}/unsubscribe?t=" in body
    assert "22 Troy Lane, Lincoln Park NJ" in body
    assert "http://t" not in body
    assert "legacy-db.example" not in body


async def test_sender_gets_footer_and_list_unsubscribe_headers(sm, app):
    """The real sender always receives the footer + RFC 8058 headers."""
    from app.api.enrichment import AutoSendIn, _auto_send_impl

    app.state.settings = _hermetic_settings(unsubscribe_base_url="https://env-override.example/")
    await _arm_with_one_contact(sm)
    calls = []

    async def sender(to, subject, body, *, headers):
        calls.append((to, body, headers))

    out = await _auto_send_impl(SimpleNamespace(app=app), AutoSendIn(dry_run=False), sender=sender)
    assert out.status == "ok"
    assert out.sent == 1
    (_to, body, headers) = calls[0]
    link = body.split("Unsubscribe: ", 1)[1].strip()
    assert link.startswith("https://env-override.example/unsubscribe?t=")
    assert headers["List-Unsubscribe"] == f"<{link}>"
    assert headers["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"


async def test_sent_link_signed_with_db_secret_verifies(sm, client):
    """A link minted from the migration-seeded DB secret (no env secret) must
    open the confirm page — not a dead 400."""
    await _arm_with_one_contact(sm)
    await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    async with sm() as s:
        log_ = (await s.execute(select(SentLog))).scalar_one()
    token = log_.body.split("/unsubscribe?t=", 1)[1].split()[0]
    r = await client.get(f"/unsubscribe?t={token}")
    assert r.status_code == 200, r.text
    assert "Confirm unsubscribe" in r.text


async def test_auto_send_refuses_when_base_url_blanked(sm, client, app):
    """A blank env override means no link can be built → refuse, zero rows."""
    app.state.settings = _hermetic_settings(unsubscribe_base_url="  ")
    await _arm_with_one_contact(sm)
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    assert r.json()["status"] == "no_unsub_config"
    assert r.json()["sent"] == 0
    async with sm() as s:
        assert (await s.execute(select(SentLog))).scalars().all() == []
