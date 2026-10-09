"""Tests for the ai-provider-layer-claude work.

Covers:
  * Both adapters round-trip (mocked, no network): JSON + text verbs
  * ClaudeProvider.search_grounded raises CapabilityUnavailable
  * get_for() honours ai_features overrides + falls back to NullProvider
  * /ai/features matrix, /ai/usage totals
  * Settings PUT validation of ai_features
  * The ai_usage.record() path writes one row per call
"""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.analysis.ai_usage_service import record
from app.config import Settings
from app.integrations.adapters.ai.provider import (
    ALLOWED_MODELS,
    DEFAULT_FEATURES,
    CapabilityUnavailable,
    ClaudeProvider,
    GeminiProvider,
    NullProvider,
    ProviderCall,
    get_for,
)
from app.main import create_app

# ----- get_for routing ------------------------------------------------------


def test_get_for_defaults_to_feature_matrix_and_null_without_keys() -> None:
    settings = Settings().model_copy(update={"gemini_api_key": None, "anthropic_api_key": None})
    p = get_for("email_drafts", settings=settings)
    # Default is Gemini; no key → NullProvider.
    assert isinstance(p, NullProvider)
    assert p.model == DEFAULT_FEATURES["email_drafts"]["model"]


def test_get_for_gemini_when_key_present() -> None:
    settings = Settings().model_copy(update={"gemini_api_key": SecretStr("fake")})
    p = get_for("email_drafts", settings=settings)
    assert isinstance(p, GeminiProvider)
    assert p.model in ALLOWED_MODELS["gemini"]


def test_get_for_claude_via_override() -> None:
    settings = Settings().model_copy(update={"anthropic_api_key": SecretStr("fake")})
    overrides = {"email_drafts": {"provider": "claude", "model": "claude-sonnet-5-5"}}
    p = get_for("email_drafts", settings=settings, ai_features=overrides)
    assert isinstance(p, ClaudeProvider)
    assert p.model in ALLOWED_MODELS["claude"]


def test_get_for_overrides_route_feature_to_gemini() -> None:
    settings = Settings().model_copy(update={"gemini_api_key": SecretStr("g")})
    overrides = {"email_drafts": {"provider": "gemini", "model": "gemini-3.5-flash-lite"}}
    p = get_for("email_drafts", settings=settings, ai_features=overrides)
    assert isinstance(p, GeminiProvider)


# ----- adapter round-trip (mocked) -----------------------------------------


@pytest.mark.asyncio
async def test_claude_adapter_generate_text_mocked() -> None:
    """ClaudeProvider wraps the SDK; mock the async client's `messages.create`."""
    p = ClaudeProvider(api_key=SecretStr("fake"), model="claude-sonnet-5-5")

    fake_response = type(
        "R",
        (),
        {
            "content": [type("B", (), {"text": "hello world"})()],
            "usage": type("U", (), {"input_tokens": 7, "output_tokens": 3})(),
        },
    )()

    with patch("anthropic.AsyncAnthropic") as cls:
        instance = cls.return_value
        instance.messages = type("M", (), {"create": AsyncMock(return_value=fake_response)})()
        call = await p.generate_text("say hi")

    assert call.status == "ok"
    assert call.text == "hello world"
    assert call.input_tokens == 7
    assert call.output_tokens == 3
    assert call.provider == "claude"
    assert call.cost_usd > Decimal(0)


@pytest.mark.asyncio
async def test_claude_adapter_generate_json_parses() -> None:
    p = ClaudeProvider(api_key=SecretStr("fake"), model="claude-sonnet-5-5")
    fake_response = type(
        "R",
        (),
        {
            "content": [type("B", (), {"text": '{"ok": true, "n": 5}'})()],
            "usage": type("U", (), {"input_tokens": 4, "output_tokens": 6})(),
        },
    )()
    with patch("anthropic.AsyncAnthropic") as cls:
        instance = cls.return_value
        instance.messages = type("M", (), {"create": AsyncMock(return_value=fake_response)})()
        call = await p.generate_json("give me json")
    assert call.status == "ok"
    assert call.parsed == {"ok": True, "n": 5}


@pytest.mark.asyncio
async def test_claude_search_grounded_raises_capability_unavailable() -> None:
    p = ClaudeProvider(api_key=SecretStr("fake"), model="claude-sonnet-5-5")
    with pytest.raises(CapabilityUnavailable):
        await p.search_grounded("find shippers")


@pytest.mark.asyncio
async def test_gemini_adapter_generate_json_mocked() -> None:
    """Mock httpx so no real network call hits Google."""
    p = GeminiProvider(api_key=SecretStr("fake"), model="gemini-3.5-flash-lite")

    class FakeResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{"content": {"parts": [{"text": '{"score": 42}'}]}}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5},
            }

    class FakeClient:
        def __init__(self, *a, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def post(self, url, params=None, json=None):
            return FakeResp()

    with patch("app.integrations.adapters.ai.provider.httpx.AsyncClient", FakeClient):
        call = await p.generate_json("score this")

    assert call.status == "ok"
    assert call.parsed == {"score": 42}
    assert call.input_tokens == 10
    assert call.output_tokens == 5
    assert call.provider == "gemini"


# ----- /ai/features ---------------------------------------------------------


@pytest.mark.asyncio
async def test_ai_features_endpoint_reports_matrix_and_key_flags() -> None:
    settings = Settings().model_copy(update={"gemini_api_key": SecretStr("g"), "anthropic_api_key": None})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/ai/features")
    assert r.status_code == 200
    body = r.json()
    assert set(body["features"].keys()) == set(DEFAULT_FEATURES.keys())
    assert body["keys_present"] == {"gemini": True, "claude": False}
    assert "claude-haiku-4-5-20251001" in body["allowed_models"]["claude"]


# ----- /ai/usage ------------------------------------------------------------


async def _create_tables(app):
    from app.db import Base

    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@pytest.mark.asyncio
async def test_ai_usage_endpoint_sums_tokens_and_cost() -> None:
    settings = Settings().model_copy(update={})
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        await _create_tables(app)

        async with app.state.sessionmaker() as s:
            # Seed five rows across providers.
            for i, (prov, model, in_tok, out_tok, cost) in enumerate(
                [
                    ("gemini", "gemini-3.5-flash-lite", 10, 20, Decimal("0.001")),
                    ("gemini", "gemini-3.5-flash-lite", 5, 5, Decimal("0.0005")),
                    ("claude", "claude-sonnet-5-5", 100, 200, Decimal("0.02")),
                    ("claude", "claude-sonnet-5-5", 50, 50, Decimal("0.01")),
                    ("claude", "claude-haiku-4-5-20251001", 10, 10, Decimal("0.0003")),
                ]
            ):
                await record(
                    s,
                    ProviderCall(
                        text="",
                        parsed=None,
                        input_tokens=in_tok,
                        output_tokens=out_tok,
                        latency_ms=1,
                        cost_usd=cost,
                        model=model,
                        provider=prov,
                        status="ok",
                    ),
                    feature="email_drafts",
                )

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/ai/usage?since=24h")
    assert r.status_code == 200
    body = r.json()
    totals = body["totals_by_provider"]
    assert totals["gemini"]["calls"] == 2
    assert totals["gemini"]["tokens"] == 40  # 10+20 + 5+5
    assert Decimal(totals["gemini"]["cost_usd"]) == Decimal("0.0015")
    assert totals["claude"]["calls"] == 3
    assert totals["claude"]["tokens"] == 420
    assert Decimal(totals["claude"]["cost_usd"]) == Decimal("0.0303")
    assert len(body["rows"]) == 5


# ----- settings PUT validation ---------------------------------------------


@pytest.mark.asyncio
async def test_settings_put_accepts_valid_ai_features_and_rejects_invalid() -> None:
    app = create_app(Settings())
    async with app.router.lifespan_context(app):
        await _create_tables(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            good = await c.put(
                "/settings",
                json={
                    "ai_features": {
                        "email_drafts": {"provider": "claude", "model": "claude-haiku-4-5-20251001"},
                    }
                },
            )
            assert good.status_code == 200, good.text
            assert good.json()["ai_features"]["email_drafts"]["model"] == "claude-haiku-4-5-20251001"

            bad_provider = await c.put(
                "/settings",
                json={"ai_features": {"email_drafts": {"provider": "openai", "model": "gpt-4"}}},
            )
            assert bad_provider.status_code == 422

            bad_model = await c.put(
                "/settings",
                json={"ai_features": {"email_drafts": {"provider": "claude", "model": "nope"}}},
            )
            assert bad_model.status_code == 422

            bad_feature = await c.put(
                "/settings",
                json={"ai_features": {"not_a_feature": {"provider": "claude", "model": "claude-sonnet-5-5"}}},
            )
            assert bad_feature.status_code == 422
