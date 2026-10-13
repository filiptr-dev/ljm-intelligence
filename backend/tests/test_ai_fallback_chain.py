"""Fallback chain for the LLMProvider seam.

Covers two beats of the ``ai-provider-fallback`` task:

  * 429 → fallback: the primary raises ``rate_limited``, the next provider
    answers, and ``last_answered_by`` is updated to the model that answered.
  * All fail → honest status: every provider in the chain returns
    ``no_api_key`` (easiest to simulate without the HTTP mock), and the
    returned call is honest (``status="no_api_key"``) — never a 500.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

import pytest
from pydantic import SecretStr

from app.config import Settings
from app.integrations.adapters.ai.provider import (
    _LAST_ANSWERED_BY,
    FallbackProvider,
    NullProvider,
    ProviderCall,
    ProviderStatus,
    get_for,
    last_answered_by,
)


@dataclass
class _FakeProvider:
    """A pretend adapter that returns a scripted ProviderCall for every verb.

    We don't need a real httpx mock here — the chain's contract is "advance
    on retryable statuses, stop on non-retryable", not "did Gemini's JSON
    body shape change". A ProviderCall-returning fake keeps the test honest
    without pulling in the whole httpx layer.
    """

    kind: str
    model: str
    call: ProviderCall

    @property
    def status(self) -> ProviderStatus:
        return ProviderStatus(ok=True, status="ok")

    async def generate_json(self, prompt, *, schema_hint=None) -> ProviderCall:
        return self.call

    async def generate_text(self, prompt) -> ProviderCall:
        return self.call

    async def search_grounded(self, prompt) -> ProviderCall:
        return self.call


def _mkcall(provider: str, model: str, status: str) -> ProviderCall:
    return ProviderCall(
        text="{}" if status == "ok" else "",
        parsed={} if status == "ok" else None,
        input_tokens=0,
        output_tokens=0,
        latency_ms=1,
        cost_usd=Decimal(0),
        model=model,
        provider=provider,
        status=status,
        error=None if status == "ok" else "simulated",
    )


@pytest.mark.asyncio
async def test_fallback_advances_on_429_and_records_last_answered() -> None:
    _LAST_ANSWERED_BY.clear()
    primary = _FakeProvider(
        kind="gemini",
        model="gemini-3.5-flash-lite",
        call=_mkcall("gemini", "gemini-3.5-flash-lite", "rate_limited"),
    )
    secondary = _FakeProvider(
        kind="gemini",
        model="gemini-3.5-flash",
        call=_mkcall("gemini", "gemini-3.5-flash", "ok"),
    )
    chain = FallbackProvider(providers=[primary, secondary], feature="inbox_analysis")

    out = await chain.generate_json("hi")

    assert out.status == "ok"
    assert out.provider == "gemini"
    assert out.model == "gemini-3.5-flash"
    assert last_answered_by()["inbox_analysis"] == "gemini:gemini-3.5-flash"


@pytest.mark.asyncio
async def test_fallback_all_fail_returns_honest_status_not_500() -> None:
    _LAST_ANSWERED_BY.clear()
    chain = FallbackProvider(
        providers=[NullProvider(model="gemini-3.5-flash-lite"), NullProvider(model="claude-sonnet-5-5")],
        feature="email_drafts",
    )

    out = await chain.generate_text("hi")

    assert out.status == "no_api_key"
    # Never recorded a successful answer when every leg is Null.
    assert "email_drafts" not in last_answered_by()


@pytest.mark.asyncio
async def test_fallback_bad_request_stops_chain() -> None:
    """A 400-ish bad request should NOT roll onto the next model: the prompt
    would fail everywhere, and silently rotating providers would just burn
    quota and hide a programmer bug."""
    _LAST_ANSWERED_BY.clear()
    primary = _FakeProvider(
        kind="gemini",
        model="gemini-3.5-flash-lite",
        call=_mkcall("gemini", "gemini-3.5-flash-lite", "bad_request"),
    )
    # Would answer OK if reached — we assert it is NOT.
    secondary = _FakeProvider(
        kind="claude",
        model="claude-sonnet-5-5",
        call=_mkcall("claude", "claude-sonnet-5-5", "ok"),
    )
    chain = FallbackProvider(providers=[primary, secondary], feature="lead_scoring")

    out = await chain.generate_text("hi")

    assert out.status == "bad_request"
    assert "lead_scoring" not in last_answered_by()


def test_get_for_builds_chain_with_configured_fallbacks() -> None:
    settings = Settings().model_copy(
        update={
            "gemini_api_key": SecretStr("g"),
            "anthropic_api_key": SecretStr("c"),
            "ai_fallback_models": "gemini:gemini-3.5-flash,claude:claude-sonnet-5-5",
        }
    )
    p = get_for("email_drafts", settings=settings)
    assert isinstance(p, FallbackProvider)
    assert [a.model for a in p.providers] == [
        "gemini-3.5-flash-lite",  # primary (feature default)
        "gemini-3.5-flash",
        "claude-sonnet-5-5",
    ]
