"""POST /email/draft: Gemini path (mocked) and fallback path both return branded HTML."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


@pytest.mark.asyncio
async def test_email_draft_fallback_when_no_gemini_key() -> None:
    from pydantic import SecretStr

    from app.config import Settings

    settings = Settings().model_copy(update={"gemini_api_key": None, "cron_secret": SecretStr("s")})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/email/draft",
                json={
                    "lead": {
                        "id": "TEST-1",
                        "name": "Acme Shipping",
                        "kind": "Shipper",
                        "city": "Newark",
                        "state": "NJ",
                        "contact_name": "Sam Rivera",
                    },
                    "tone": "friendly",
                },
            )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert "Acme Shipping" in body["subject"] or "Acme Shipping" in body["body"]
    assert "LJM International" in body["body"]
    # Branded HTML always applied
    assert "<!DOCTYPE html>" in body["body_html"]
    assert "22 Troy Lane" in body["body_html"]
    assert "#BC2444" in body["body_html"]


@pytest.mark.asyncio
async def test_email_draft_uses_gemini_when_key_present() -> None:
    from decimal import Decimal

    from pydantic import SecretStr

    from app.config import Settings
    from app.integrations.adapters.ai.provider import ProviderCall

    settings = Settings().model_copy(
        update={"gemini_api_key": SecretStr("fake-key"), "anthropic_api_key": SecretStr("fake-key")}
    )
    app = create_app(settings)

    async def fake_generate_json(self, prompt, *, schema_hint=None):
        return ProviderCall(
            text='{"subject":"Dry-van coverage this week","body":"Hi Sam,\\n\\nQuick note about a truck we can send your way.\\n\\n- Nick"}',
            parsed={
                "subject": "Dry-van coverage this week",
                "body": "Hi Sam,\n\nQuick note about a truck we can send your way.\n\n- Nick",
            },
            input_tokens=10,
            output_tokens=20,
            latency_ms=5,
            cost_usd=Decimal("0.000001"),
            model="gemini-3.5-flash-lite",
            provider="gemini",
            status="ok",
        )

    # email_drafts defaults to Gemini; patch Gemini's seam.
    with patch("app.integrations.adapters.ai.provider.GeminiProvider.generate_json", new=fake_generate_json):
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post(
                    "/email/draft",
                    json={
                        "lead": {"name": "Beta Freight", "kind": "Broker", "contact_name": "Sam"},
                        "tone": "direct",
                        "instructions": "mention we run flatbed too",
                    },
                )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "gemini"
    assert body["subject"] == "Dry-van coverage this week"
    assert "Quick note" in body["body"]
    assert "22 Troy Lane" in body["body_html"]


@pytest.mark.asyncio
async def test_email_draft_falls_back_on_gemini_error() -> None:
    """Gemini 429/5xx must not surface as a 500 — the composer needs a body no matter what."""
    from decimal import Decimal

    from pydantic import SecretStr

    from app.config import Settings
    from app.integrations.adapters.ai.provider import ProviderCall

    settings = Settings().model_copy(
        update={"gemini_api_key": SecretStr("fake-key"), "anthropic_api_key": SecretStr("fake-key")}
    )
    app = create_app(settings)

    async def boom(self, prompt, *, schema_hint=None):
        return ProviderCall(
            text="",
            parsed=None,
            input_tokens=0,
            output_tokens=0,
            latency_ms=0,
            cost_usd=Decimal(0),
            model="gemini-3.5-flash-lite",
            provider="gemini",
            status="rate_limited",
            error="upstream 429",
        )

    with patch("app.integrations.adapters.ai.provider.GeminiProvider.generate_json", new=boom):
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post(
                    "/email/draft",
                    json={"lead": {"name": "Delta Logistics", "kind": "Forwarder"}, "tone": "professional"},
                )
    assert r.status_code == 200
    body = r.json()
    assert body["source"] == "fallback"
    assert "Delta Logistics" in body["body"]


# --- Stance-aware drafts for existing brokers (fallback path only, Gemini key forced off) ---


def _no_gemini_app():
    from pydantic import SecretStr

    from app.config import Settings

    settings = Settings().model_copy(update={"gemini_api_key": None, "cron_secret": SecretStr("s")})
    return create_app(settings)


async def _draft(lead: dict, tone: str = "professional") -> dict:
    app = _no_gemini_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/email/draft", json={"lead": lead, "tone": tone})
    assert r.status_code == 200, r.text
    return r.json()


BROKER = {
    "id": "b090",
    "name": "Summit Freight",
    "kind": "Broker",
    "contact_name": "Dana Cole",
    "top_lane": "Newark, NJ -> Atlanta, GA",
    "booked": 14,
}


@pytest.mark.asyncio
async def test_positive_sentiment_gives_availability_pitch() -> None:
    body = await _draft({**BROKER, "sentiment": 0.55, "days_since_last": 6, "health_delta": 4})
    assert body["stance"] == "positive"
    assert body["source"] == "fallback"
    assert body["subject"].startswith("Truck open this week")
    assert "14 loads" in body["body"]
    assert "hold it for you" in body["body"]


@pytest.mark.asyncio
async def test_neutral_sentiment_gives_check_in() -> None:
    body = await _draft({**BROKER, "sentiment": 0.05, "days_since_last": 20, "health_delta": 0})
    assert body["stance"] == "neutral"
    assert body["subject"].startswith("Checking in from LJM International")
    assert "coming up" in body["body"]


@pytest.mark.asyncio
async def test_negative_sentiment_gives_soft_reengage() -> None:
    body = await _draft(
        {**BROKER, "sentiment": -0.4, "days_since_last": 75, "health_delta": -18, "top_reason": "Rate too high"}
    )
    assert body["stance"] == "cooling"
    assert body["subject"] == "Anything we can do better, Dana?"
    assert "75 days" in body["body"]
    assert "rate too high" in body["body"]
    assert "another shot" in body["body"]


@pytest.mark.asyncio
async def test_long_silence_alone_is_cooling_and_explicit_stance_wins() -> None:
    quiet = await _draft({**BROKER, "sentiment": 0.5, "days_since_last": 90})
    assert quiet["stance"] == "cooling"
    forced = await _draft({**BROKER, "sentiment": -0.9, "stance": "positive"})
    assert forced["stance"] == "positive"


@pytest.mark.asyncio
async def test_cold_lead_without_relationship_data_keeps_old_template() -> None:
    body = await _draft({"name": "Acme Shipping", "kind": "Shipper", "city": "Newark", "state": "NJ"})
    assert body["stance"] is None
    assert body["subject"] == "Direct dry-van capacity for Acme Shipping"
