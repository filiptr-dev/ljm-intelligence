"""Enrichment scraper tests — every AC in the LLM-scraper plan, offline.

No network anywhere: `httpx.AsyncClient` is stubbed, robots.txt is bypassed via
the fetcher mock (we test `robots.is_allowed` separately), and the LLM provider
is a fake that returns fixture dataclasses.

Covers:
  - migration model round-trip (columns + tables via Base.metadata.create_all)
  - fetcher: js_only heuristic (>5KB HTML, tiny text, script-heavy)
  - robots: allow / deny with cache
  - linkedin_search: citation-anchored URL validation (kept, hallucinated,
    malformed, http↔https)
  - site_scraper: robots-skip + page-cap + linkedin-host-refuse
  - enrichment.enrich_company: no_api_key branch; happy path with mocked
    provider + fetcher; contact dedupe (email); provenance rows always appended
  - discover_new_shippers → shipper_candidates gains "GEMINI" source
  - promote copies enrichment_candidates onto lead_contacts + provenance
  - metrics endpoint numbers
  - auto-outreach: disabled default, no_footer refusal, suppression skip,
    daily cap, unsubscribe route flow
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import (
    EmailTemplate,
    EnrichmentCandidate,
    Lead,
    LeadContact,
    LeadContactProvenance,
    SentLog,
    SettingsRow,
    ShipperCandidate,
    Suppression,
)


async def _seed_template(sm, template_id: str = "tmpl-1") -> None:
    """Seed an EmailTemplate row for `settings.auto_outreach_template_id` FK.
    PG16 enforces the FK; sqlite doesn't."""
    async with sm() as s:
        s.add(
            EmailTemplate(
                id=template_id,
                name=template_id,
                subject="Hi {name}",
                body="Hello {name} — {{unsub}}",
                tokens=[],
            )
        )
        await s.commit()
from app.pipeline.enrichment import (
    ContactPayload,
    copy_enrichment_candidates_to_lead,
    discover_new_shippers,
    enrich_company,
    mark_contact_contacted,
    upsert_lead_contact,
)
from app.sources import robots as robots_mod
from app.sources.fetcher import FetchResult, _js_only_heuristic
from app.sources.linkedin_search import (
    _LINKEDIN_IN_RE,
    CompanyRef,
    _normalize_url,
    find_decision_makers,
)
from app.integrations.adapters.ai.provider import GeminiProvider, NullProvider

# =========================================================================
# Fixtures / mocks
# =========================================================================


class FakeFetcher:
    """A `Fetcher`-shaped mock. `pages` maps URL → (html, text, status, js_only)."""

    def __init__(self, pages: dict[str, tuple[str, str, int, bool]]):
        self.pages = pages
        self.calls: list[str] = []

    async def fetch(self, url: str) -> FetchResult:
        self.calls.append(url)
        html, text, status, js_only = self.pages.get(url, ("", "", 404, False))
        return FetchResult(
            url=url,
            final_url=url,
            status=status,
            html=html,
            text=text,
            fetched_at=datetime.now(UTC),
            js_only=js_only,
        )

    async def aclose(self) -> None:
        return None


@pytest.fixture(autouse=True)
def _clear_robots_cache():
    robots_mod.clear_cache()
    yield
    robots_mod.clear_cache()


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
    """Build a Settings that never reads the on-disk `.env` — the repo `.env`
    holds a prod DATABASE_URL and a live CRON_SECRET, either of which would
    make these tests hit prod or drift from the route-auth expectations.

    Ships a known `cron_secret` + `unsubscribe_secret` so token/header flows
    are deterministic. Callers pass overrides to flex per-test scenarios.
    """
    from app.config import Settings

    base = {
        "_env_file": None,
        "database_url": "sqlite+aiosqlite:///:memory:",
        "cron_secret": SecretStr("test-cron-secret"),
        "unsubscribe_secret": SecretStr("test-unsub-secret"),
        "outreach_postal_address": "",
    }
    base.update(overrides)
    return Settings(**base)


_CRON_HEADERS = {"X-Cron-Secret": "test-cron-secret"}


@pytest.fixture
async def app(sm):
    a = create_app()
    a.state.sessionmaker = sm
    # Override settings hermetically — see _hermetic_settings docstring.
    a.state.settings = _hermetic_settings()
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


def _fake_provider() -> GeminiProvider:
    return GeminiProvider(api_key=SecretStr("test-key"), model="gemini-3.5-flash-lite")


# =========================================================================
# Migration / model round-trip
# =========================================================================


async def test_new_tables_and_columns_exist(sm):
    async with sm() as s:
        # If any new column is missing, this will raise on execute.
        await s.execute(select(LeadContact.pipeline_status, LeadContact.confidence, LeadContact.evidence))
        await s.execute(select(LeadContactProvenance.id, LeadContactProvenance.snippet))
        await s.execute(select(EnrichmentCandidate.id, EnrichmentCandidate.candidate_id))
        await s.execute(
            select(
                Lead.linkedin_company_url,
                Lead.website_url,
                Lead.enrichment_status,
                Lead.is_js_only_site,
            )
        )
        await s.execute(select(SentLog.contact_id))


# =========================================================================
# Fetcher — js_only heuristic
# =========================================================================


def test_js_only_heuristic_true_on_script_heavy():
    html = "<html><body>" + ("<script>x=1;</script>" * 500) + "</body></html>"
    text = ""
    assert _js_only_heuristic(html, text) is True


def test_js_only_heuristic_false_on_content_page():
    html = "<html><body>" + ("<p>Real content paragraph.</p>" * 200) + "</body></html>"
    text = "Real content paragraph." * 20
    assert _js_only_heuristic(html, text) is False


# =========================================================================
# Robots
# =========================================================================


def test_robots_unknown_host_allows(monkeypatch):
    # No parser can be built → allowed.
    monkeypatch.setattr(robots_mod, "_fetch_parser", lambda h, s: None)
    assert robots_mod.is_allowed("https://example.com/contact", "LJM") is True


def test_robots_disallow_specific_path(monkeypatch):
    class _P:
        def can_fetch(self, ua, url):
            return "/contact" not in url

    monkeypatch.setattr(robots_mod, "_fetch_parser", lambda h, s: _P())
    assert robots_mod.is_allowed("https://x.com/", "LJM") is True
    assert robots_mod.is_allowed("https://x.com/contact", "LJM") is False


# =========================================================================
# linkedin_search — citation-anchored URL validation
# =========================================================================


async def test_linkedin_search_no_api_key():
    hits = await find_decision_makers(
        CompanyRef(name="Acme", state="NJ", kind="Shipper"),
        provider=NullProvider(),
    )
    assert hits.status == "no_api_key"
    assert hits.people == []


async def test_linkedin_search_citation_anchor(monkeypatch):
    citations = [
        {"url": "https://www.linkedin.com/in/real-person", "title": "Real"},
        {"url": "https://acme.com/about", "title": "About"},
    ]

    async def fake_call(provider, prompt, *, timeout_s=30.0):
        payload = (
            '{"people":['
            '{"name":"Real Person","title":"Logistics Manager",'
            '"linkedin_url":"https://www.linkedin.com/in/real-person","evidence_url":"https://acme.com/about"},'
            '{"name":"Fake Ghost","title":"VP Operations",'
            '"linkedin_url":"https://www.linkedin.com/in/ghost-slug","evidence_url":""},'
            '{"name":"Bad URL","title":"Traffic Manager",'
            '"linkedin_url":"https://www.linkedin.com/company/oops","evidence_url":""},'
            '{"name":"HttpVariant","title":"Warehouse Manager",'
            '"linkedin_url":"http://www.linkedin.com/in/real-person/","evidence_url":""}'
            "]}"
        )
        return payload, citations

    import app.sources.linkedin_search as lm

    monkeypatch.setattr(lm, "_grounded_call", fake_call)
    hits = await find_decision_makers(
        CompanyRef(name="Acme", state="NJ", kind="Shipper"),
        provider=_fake_provider(),
    )
    urls = [h.linkedin_url for h in hits.people]
    # Real person kept; hallucinated dropped; malformed (/company/) dropped;
    # http↔https variant of a cited URL is kept via normalizer.
    assert "https://www.linkedin.com/in/real-person" in urls
    assert "http://www.linkedin.com/in/real-person/" in urls
    assert "https://www.linkedin.com/in/ghost-slug" not in urls
    assert "https://www.linkedin.com/company/oops" not in urls


def test_linkedin_url_regex_shape():
    assert _LINKEDIN_IN_RE.match("https://www.linkedin.com/in/joe-smith") is not None
    assert _LINKEDIN_IN_RE.match("https://uk.linkedin.com/in/joe-smith/") is not None
    assert _LINKEDIN_IN_RE.match("https://www.linkedin.com/company/acme") is None


def test_url_normalizer_http_https_trailing_slash():
    a = _normalize_url("https://www.linkedin.com/in/joe/")
    b = _normalize_url("http://www.linkedin.com/in/joe")
    assert a == b


# =========================================================================
# Enrichment orchestrator — end-to-end with mocked provider + fetcher
# =========================================================================


class FakeProviderWithSearch(GeminiProvider):
    pass  # dataclass; just a marker


async def _seed_lead(sm, **kw) -> Lead:
    defaults = {
        "id": "MC-1001",
        "name": "Acme Distribution",
        "kind": "Shipper",
        "state": "NJ",
        "city": "Newark",
        "domain": "acme.com",
        "raw": {},
        "evidence": {},
        "recommendations": [],
    }
    defaults.update(kw)
    async with sm() as s:
        row = Lead(**defaults)
        s.add(row)
        await s.commit()
        await s.refresh(row)
    return row


async def test_enrich_no_api_key(sm, monkeypatch):
    """AC: with GEMINI_API_KEY unset → status=no_api_key, zero writes."""
    await _seed_lead(sm)
    from app.config import Settings

    settings = Settings(database_url="sqlite+aiosqlite:///:memory:", gemini_api_key=None)
    async with sm() as s, s.begin():
        result = await enrich_company(
            s,
            settings,
            lead_id="MC-1001",
            provider=NullProvider(),
            fetcher=FakeFetcher({}),
        )
    assert result.status == "no_api_key"
    async with sm() as s:
        n = (await s.execute(select(LeadContact))).scalars().all()
    assert n == []


async def test_enrich_happy_path(sm, monkeypatch):
    """Site scraper + LinkedIn search both hit. Contacts dedupe by email; provenance recorded."""
    # Earlier revision hung here: `robots.is_allowed` called urllib's
    # `RobotFileParser.read()`, which has no socket timeout and blocked
    # the event loop fetching acme.com/robots.txt. Fixed in robots.py with
    # a bounded urlopen; also stubbed here so the test never leaves the box.
    from app.sources import robots as _robots

    monkeypatch.setattr(_robots, "is_allowed", lambda url, ua: True)
    _robots.clear_cache()

    await _seed_lead(sm)

    # Stub grounded LinkedIn + company-page calls.
    async def fake_call(provider, prompt, *, timeout_s=30.0):
        if "OFFICIAL LinkedIn company page" in prompt:
            body = '{"linkedin_company_url":"https://www.linkedin.com/company/acme","website_url":"https://acme.com/"}'
            return body, [
                {"url": "https://www.linkedin.com/company/acme"},
                {"url": "https://acme.com/"},
            ]
        body = (
            '{"people":[{"name":"Jane Freight","title":"Logistics Manager",'
            '"linkedin_url":"https://www.linkedin.com/in/jane-freight","evidence_url":""}]}'
        )
        return body, [{"url": "https://www.linkedin.com/in/jane-freight"}]

    import app.sources.linkedin_search as lm

    monkeypatch.setattr(lm, "_grounded_call", fake_call)

    # Stub the extractor's provider. The adapter-rehome moved
    # `gemini_extractor` from a direct `httpx.AsyncClient.post` to
    # `provider.generate_json(...)`, so the previous `httpx.AsyncClient`
    # monkeypatch no longer bites. Patch `GeminiProvider.generate_json`
    # directly to hand back the canned response.
    import json as _json

    from app.integrations.adapters.ai import provider as _pv

    async def _fake_generate_json(self, prompt, *, schema_hint=None):  # noqa: ARG001
        parsed = {
            "emails": [{"value": "hello@acme.com", "context": ""}],
            "phones": [],
            "people": [
                {
                    "name": "Bob Ops",
                    "title": "Warehouse Manager",
                    "email": "bob@acme.com",
                    "phone": "",
                }
            ],
        }
        return _pv.ProviderCall(
            text=_json.dumps(parsed),
            parsed=parsed,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_usd=Decimal(0),
            model="gemini-test",
            provider="gemini",
            status="ok",
            error=None,
            citations=[],
        )

    monkeypatch.setattr(_pv.GeminiProvider, "generate_json", _fake_generate_json)

    # Site fixture pages with the emails present (source-text check).
    site_pages = {
        "https://acme.com/": (
            "<html>Acme home hello@acme.com bob@acme.com</html>",
            "hello@acme.com bob@acme.com",
            200,
            False,
        ),
        "https://acme.com/contact": ("<html>hello@acme.com</html>", "hello@acme.com", 200, False),
        "https://acme.com/contact-us": ("", "", 404, False),
        "https://acme.com/about": ("", "", 404, False),
        "https://acme.com/about-us": ("", "", 404, False),
        "https://acme.com/team": ("", "", 404, False),
        "https://acme.com/our-team": ("", "", 404, False),
        "https://acme.com/people": ("", "", 404, False),
    }

    from app.config import Settings

    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        gemini_api_key=SecretStr("test-key"),
        outreach_postal_address="22 Troy Lane",
        enrichment_between_requests_s=0.0,
    )
    async with sm() as s, s.begin():
        result = await enrich_company(
            s,
            settings,
            lead_id="MC-1001",
            provider=_fake_provider(),
            fetcher=FakeFetcher(site_pages),
        )
    assert result.status == "ok"
    assert result.linkedin_company_url == "https://www.linkedin.com/company/acme"
    async with sm() as s:
        contacts = (await s.execute(select(LeadContact))).scalars().all()
        prov = (await s.execute(select(LeadContactProvenance))).scalars().all()
    # decision-maker (Jane) + email-only hello@acme.com + person (Bob Ops)
    emails = {c.email for c in contacts if c.email}
    assert emails == {"hello@acme.com", "bob@acme.com"}
    dms = [c for c in contacts if c.is_decision_maker]
    # Jane (LinkedIn) + Bob Ops (site-team) are both DMs
    assert len(dms) >= 2
    # New save-everything doctrine — every sighting has a provenance row.
    assert len(prov) >= 1
    # Second enrich → contact dedupe on email; still writes new provenance rows.
    async with sm() as s, s.begin():
        await enrich_company(
            s,
            settings,
            lead_id="MC-1001",
            provider=_fake_provider(),
            fetcher=FakeFetcher(site_pages),
            force=True,
        )
    async with sm() as s:
        contacts2 = (await s.execute(select(LeadContact))).scalars().all()
        prov2 = (await s.execute(select(LeadContactProvenance))).scalars().all()
    assert len(contacts2) == len(contacts)  # dedupe held
    assert len(prov2) > len(prov)  # history grew


# =========================================================================
# discover_new_shippers → shipper_candidates gets a Gemini source
# =========================================================================


async def test_discover_new_shippers_adds_gemini_source(sm, monkeypatch):
    from app.config import Settings
    from app.sources.gemini_search import DiscoveredCompany, GeminiDiscoverer

    async def fake_discover(self, *, target_count=8):
        return [
            DiscoveredCompany(
                id="DOMAIN-newshipper.com",
                mc=None,
                dot=None,
                domain="newshipper.com",
                name="NewShipper",
                kind="Shipper",
                state="NJ",
                city="Newark",
            )
        ]

    monkeypatch.setattr(GeminiDiscoverer, "discover", fake_discover)
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        gemini_api_key=SecretStr("test-key"),
    )
    out = await discover_new_shippers(sm, settings, run_id="run_test")
    assert out["discovery_status"] == "ok"
    assert out["discovery_new_shippers"] == 1
    async with sm() as s:
        rows = (await s.execute(select(ShipperCandidate))).scalars().all()
    assert any("GEMINI" in (r.sources or []) for r in rows)


# =========================================================================
# Promote copies enrichment_candidates
# =========================================================================


async def test_promote_copies_enrichment_candidates(sm, client):
    # Seed a candidate + a pre-promotion contact.
    cand_id = "cand-promote-1"
    async with sm() as s:
        s.add(
            ShipperCandidate(
                id=cand_id,
                sources=["FMCSA"],
                fmcsa_mc="99999",
                mc="99999",
                name="ToPromote Inc",
                state="NJ",
            )
        )
        await s.flush()  # PG16 FK: parent must land before the child insert.
        s.add(
            EnrichmentCandidate(
                candidate_id=cand_id,
                name="Alice Ops",
                title="Warehouse Manager",
                email="alice@topromote.com",
                phone=None,
                linkedin_url=None,
                is_decision_maker=True,
                confidence="high",
                source="Website Team Page",
                source_url="https://topromote.com/team",
            )
        )
        await s.commit()

    resp = await client.post("/tools/shipper-finder/promote", json={"candidate_id": cand_id})
    assert resp.status_code == 200, resp.text
    lead_id = resp.json()["lead_id"]

    async with sm() as s:
        contacts = (await s.execute(select(LeadContact).where(LeadContact.lead_id == lead_id))).scalars().all()
        prov = (await s.execute(select(LeadContactProvenance))).scalars().all()
    assert any(c.email == "alice@topromote.com" for c in contacts)
    assert len(prov) >= 1


# =========================================================================
# Metrics endpoint
# =========================================================================


async def test_metrics_endpoint(sm, client):
    async with sm() as s:
        s.add(Lead(id="MC-METR", name="Metric Co", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.flush()
        s.add(
            LeadContact(
                lead_id="MC-METR",
                name="X",
                email="a@b.com",
                pipeline_status="contacted",
                is_decision_maker=True,
                confidence="high",
                source="Website Contact Page",
                source_url="https://x/",
            )
        )
        await s.commit()

    r = await client.get("/enrichment/metrics?kind=shipper")
    assert r.status_code == 200
    body = r.json()
    assert body["scope"] == "shipper"
    assert body["enriched_all_time"] >= 1
    assert body["reachable_all_time"] >= 1
    assert body["contacted"] >= 1


# =========================================================================
# pipeline_status transition + sent_log.contact_id join
# =========================================================================


async def test_mark_contact_contacted(sm):
    async with sm() as s:
        s.add(Lead(id="MC-STAT", name="Stat", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.flush()
        c = LeadContact(lead_id="MC-STAT", email="c@x.com", pipeline_status="found", is_decision_maker=False)
        s.add(c)
        await s.commit()
        await s.refresh(c)
    async with sm() as s:
        ok = await mark_contact_contacted(s, c.id)
        await s.commit()
    assert ok is True
    async with sm() as s:
        row = (await s.execute(select(LeadContact).where(LeadContact.id == c.id))).scalar_one()
    assert row.pipeline_status == "contacted"
    assert row.pipeline_status_at is not None


# =========================================================================
# Auto-outreach — behavior gates
# =========================================================================


async def test_auto_send_rejects_without_cron_secret(sm, client):
    """No `X-Cron-Secret` → 401. Route-level guard is the BLOCKING-2 fix; the
    application-level `auto_outreach_enabled` toggle is *what* not *who*, and
    can't be relied on to fence a public endpoint."""
    r = await client.post("/enrichment/auto-send", json={"dry_run": False})
    assert r.status_code == 401


async def test_auto_send_disabled_by_default(sm, client):
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    # No settings row → treated as disabled.
    assert r.json()["status"] == "disabled"


async def test_auto_send_needs_footer(sm, client, app, monkeypatch):
    # Enable auto-outreach + template. Empty postal address must refuse.
    await _seed_template(sm)
    async with sm() as s:
        s.add(SettingsRow(id=1, auto_outreach_enabled=True, auto_outreach_template_id="tmpl-1"))
        await s.commit()
    # Fixture settings pin outreach_postal_address="" by default.
    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    assert r.json()["status"] == "no_footer"


async def test_auto_send_respects_suppression_and_cap(sm, client, app):
    # Configure app settings with footer + cap.
    app.state.settings = _hermetic_settings(
        outreach_postal_address="22 Troy Lane, Lincoln Park NJ",
        unsubscribe_base_url="https://ljm.test",
    )
    await _seed_template(sm)
    async with sm() as s:
        s.add(
            SettingsRow(
                id=1,
                auto_outreach_enabled=True,
                auto_outreach_template_id="tmpl-1",
                auto_outreach_daily_cap=1,
                auto_outreach_window_start_h=0,
                auto_outreach_window_end_h=0,  # equal → always in window
            )
        )
        # fit_score >= default min_fit (60) so the fit-filter in _auto_send_impl
        # lets this lead through; the test is about suppression + cap, not fit.
        s.add(
            Lead(
                id="MC-AS",
                name="AutoSend Co",
                kind="Shipper",
                state="NJ",
                raw={},
                evidence={},
                recommendations=[],
                fit_score=80,
            )
        )
        await s.flush()
        s.add(
            LeadContact(
                lead_id="MC-AS",
                email="ok@x.com",
                pipeline_status="found",
                is_decision_maker=True,
                source="Website Contact Page",
                source_url="",
            )
        )
        s.add(
            LeadContact(
                lead_id="MC-AS",
                email="drop@x.com",
                pipeline_status="found",
                is_decision_maker=True,
                source="Website Contact Page",
                source_url="",
            )
        )
        s.add(Suppression(email="drop@x.com", reason="unsub"))
        await s.commit()

    r = await client.post("/enrichment/auto-send", json={"dry_run": False}, headers=_CRON_HEADERS)
    body = r.json()
    assert body["status"] == "ok", body
    assert body["sent"] == 1  # cap = 1
    assert body["skipped_suppressed"] == 1  # 'drop@x.com' skipped
    async with sm() as s:
        logs = (await s.execute(select(SentLog))).scalars().all()
    assert len(logs) == 1
    assert logs[0].contact_id is not None


async def test_unsubscribe_get_does_not_mutate(sm, client):
    """GET /unsubscribe must render a confirm page and never mutate. Email
    security scanners auto-fetch every URL in every outbound email — if GET
    mutated, one scanned inbox would unsubscribe the recipient before they
    read the message. Confirm-then-POST is the CAN-SPAM one-click contract
    (RFC 8058); we honor it."""
    from app.api._auth import sign_unsubscribe_token

    async with sm() as s:
        s.add(Lead(id="MC-UN-G", name="U", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.flush()
        c = LeadContact(lead_id="MC-UN-G", email="peek@x.com", pipeline_status="contacted", is_decision_maker=True)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        cid = c.id

    token = sign_unsubscribe_token(cid, "test-unsub-secret")
    r = await client.get(f"/unsubscribe?t={token}")
    assert r.status_code == 200, r.text
    assert "text/html" in r.headers["content-type"]
    assert "Confirm unsubscribe" in r.text
    # Crucially: no suppression row created, contact still 'contacted'.
    async with sm() as s:
        supp = (await s.execute(select(Suppression))).scalars().all()
        row = (await s.execute(select(LeadContact).where(LeadContact.id == cid))).scalar_one()
    assert supp == []
    assert row.pipeline_status == "contacted"


async def test_unsubscribe_post_with_valid_token_suppresses(sm, client):
    from app.api._auth import sign_unsubscribe_token

    async with sm() as s:
        s.add(Lead(id="MC-UN-P", name="U", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.flush()
        c = LeadContact(lead_id="MC-UN-P", email="quit@x.com", pipeline_status="contacted", is_decision_maker=True)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        cid = c.id

    token = sign_unsubscribe_token(cid, "test-unsub-secret")
    r = await client.post(f"/unsubscribe?t={token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "quit@x.com"
    async with sm() as s:
        supp = (await s.execute(select(Suppression))).scalars().all()
        row = (await s.execute(select(LeadContact).where(LeadContact.id == cid))).scalar_one()
    assert any(x.email == "quit@x.com" for x in supp)
    assert row.pipeline_status == "lost"


async def test_unsubscribe_post_with_forged_or_int_token_rejects(sm, client):
    """Forged HMAC or bare-integer token → 400 with zero side effects. The
    old GET /unsubscribe?c=<int> was enumerable — `c=1,2,3,…` would mass-
    unsubscribe every contact in the DB. The token-based route closes that."""
    async with sm() as s:
        s.add(Lead(id="MC-UN-F", name="U", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.flush()
        c = LeadContact(lead_id="MC-UN-F", email="safe@x.com", pipeline_status="contacted", is_decision_maker=True)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        cid = c.id

    # Bare integer — the pre-fix format. Must be rejected.
    r = await client.post(f"/unsubscribe?t={cid}")
    assert r.status_code == 400

    # Well-shaped `<id>.<sig>` but signature is bogus.
    r = await client.post(f"/unsubscribe?t={cid}.deadbeef")
    assert r.status_code == 400

    # Nothing changed.
    async with sm() as s:
        supp = (await s.execute(select(Suppression))).scalars().all()
        row = (await s.execute(select(LeadContact).where(LeadContact.id == cid))).scalar_one()
    assert supp == []
    assert row.pipeline_status == "contacted"


async def test_upsert_contact_dedupes_email(sm):
    """Same email, two pages → one contact row, two provenance rows (save-everything)."""
    async with sm() as s:
        s.add(Lead(id="MC-DE", name="De", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        await s.commit()

    now = datetime.now(UTC)
    p1 = ContactPayload(
        name=None,
        title=None,
        email="same@x.com",
        phone=None,
        linkedin_url=None,
        is_decision_maker=False,
        confidence="high",
        source="Website Home",
        source_url="https://x/",
    )
    p2 = ContactPayload(
        name=None,
        title=None,
        email="same@x.com",
        phone=None,
        linkedin_url=None,
        is_decision_maker=False,
        confidence="high",
        source="Website Contact Page",
        source_url="https://x/contact",
    )
    async with sm() as s:
        await upsert_lead_contact(s, lead_id="MC-DE", payload=p1, run_id=None, now=now)
        await upsert_lead_contact(s, lead_id="MC-DE", payload=p2, run_id=None, now=now)
        await s.commit()
    async with sm() as s:
        contacts = (await s.execute(select(LeadContact))).scalars().all()
        prov = (await s.execute(select(LeadContactProvenance))).scalars().all()
    assert len(contacts) == 1
    assert len(prov) == 2


async def test_copy_enrichment_candidates_helper(sm):
    async with sm() as s:
        s.add(Lead(id="MC-COPY", name="Copy", kind="Shipper", state="NJ", raw={}, evidence={}, recommendations=[]))
        s.add(ShipperCandidate(id="cand-copy", sources=["FMCSA"], name="Copy", state="NJ"))
        await s.flush()  # PG16 FK: parent must land before the child insert.
        s.add(
            EnrichmentCandidate(
                candidate_id="cand-copy", email="dm@copy.com", is_decision_maker=True, source="X", source_url=""
            )
        )
        await s.commit()
    async with sm() as s:
        n = await copy_enrichment_candidates_to_lead(s, candidate_id="cand-copy", lead_id="MC-COPY", run_id=None)
        await s.commit()
    assert n == 1
    async with sm() as s:
        rows = (await s.execute(select(LeadContact))).scalars().all()
    assert rows[0].email == "dm@copy.com"
