"""LinkedInLoadsSource unit tests — fake provider, no network, no Gemini.

Pins three invariants:
  * citation-anchored URL rule (hallucinated LinkedIn URLs are dropped),
  * state-code validation + the 32-state in-region filter,
  * ``RawLoad`` shape (``source='linkedin'``, ``source_ref=<post url>``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

import pytest
from pydantic import SecretStr

from app.integrations.adapters.ai.provider import ProviderCall
from app.integrations.adapters.loadboard.linkedin_source import LinkedInLoadsSource


@dataclass
class _FakeSettings:
    gemini_api_key: SecretStr = field(default_factory=lambda: SecretStr("test-key"))
    loads_agent_kill: str = ""
    linkedin_loads_target: int = 10


class _FakeProvider:
    def __init__(self, text: str, citations: list[dict]):
        self._text = text
        self._citations = citations

    async def search_grounded(self, prompt: str) -> ProviderCall:
        return ProviderCall(
            text=self._text,
            parsed=None,
            input_tokens=10,
            output_tokens=10,
            latency_ms=1,
            cost_usd=Decimal(0),
            model="fake",
            provider="fake",
            status="ok",
            error=None,
            citations=self._citations,
        )


_PAYLOAD = (
    '{"loads":['
    '{"linkedin_url":"https://www.linkedin.com/posts/acme-logistics_load-abc123",'
    '"broker_name":"ACME Logistics","origin_city":"Chicago","origin_state":"IL",'
    '"dest_city":"Atlanta","dest_state":"GA","equipment":"Dry Van",'
    '"rate_usd":"2400","miles":720,"pickup_date":"2026-10-15","snippet":"Pickup Mon"},'
    '{"linkedin_url":"https://www.linkedin.com/posts/hallucinated_HALLUC",'
    '"broker_name":"Fake","origin_state":"IL","dest_state":"GA"},'
    '{"linkedin_url":"https://www.linkedin.com/posts/west-only_WEST",'
    '"broker_name":"West","origin_state":"CA","dest_state":"TX"}'
    "]}"
)
_CITATIONS = [
    {"url": "https://www.linkedin.com/posts/acme-logistics_load-abc123"},
    {"url": "https://www.linkedin.com/posts/west-only_WEST"},  # cited but out-of-region
]


@pytest.mark.asyncio
async def test_fetch_filters_and_shapes(monkeypatch):
    import app.integrations.adapters.loadboard.linkedin_source as mod

    monkeypatch.setattr(
        mod, "get_for", lambda *a, **kw: _FakeProvider(_PAYLOAD, _CITATIONS)
    )
    src = LinkedInLoadsSource(_FakeSettings())
    assert src.enabled is True
    raws = await src.fetch(_FakeSettings())

    # The hallucinated row is dropped (not in citations); the out-of-region
    # row (CA↔TX) is dropped by the in-region filter; one row survives.
    assert len(raws) == 1
    r = raws[0]
    assert r.source == "linkedin"
    assert r.source_ref == "https://www.linkedin.com/posts/acme-logistics_load-abc123"
    assert r.broker_name == "ACME Logistics"
    assert r.origin_state == "IL" and r.dest_state == "GA"
    assert r.rate_usd == 2400.0
    assert r.miles == 720
    assert r.raw["linkedin_url"] == r.source_ref


@pytest.mark.asyncio
async def test_disabled_without_api_key():
    src = LinkedInLoadsSource(_FakeSettings(gemini_api_key=SecretStr("")))
    assert src.enabled is False
    assert src.reason() == "no_api_key"
    assert await src.fetch(_FakeSettings(gemini_api_key=SecretStr(""))) == []


@pytest.mark.asyncio
async def test_disabled_by_kill_switch():
    src = LinkedInLoadsSource(_FakeSettings(loads_agent_kill="1"))
    assert src.enabled is False
    assert src.reason() == "agent_killed"
