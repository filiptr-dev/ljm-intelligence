"""Lane AI insights: cache hit, provider null, parse failure, strict schema, per-entity keys."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis import lanes_ai_service as ai, lanes_history_service as H
from app.analysis.models import LaneInsightsCache
from app.db import Base
from app.shared.untrusted import FENCE_CLOSE, FENCE_OPEN
from tests.analysis.lanes_fixtures import NOW, TENANT, seed

pytestmark = pytest.mark.asyncio
SCOPE = H.Scope(tenant_id=TENANT)

GOOD = {
    "focus_lanes": [{"lane": "Chicago -> Atlanta", "why": "rate/mi up 8.1% on 25% more runs",
                     "metric": "$/mi", "delta_pct": 8.1}],
    "declining_lanes": [{"lane": "Dallas -> Memphis", "why": "rate/mi down 11%", "evidence": {"metric": "$/mi", "delta_pct": -11}}],
    "market_shifts": [{"headline": "Nearshoring lane is growing", "evidence": "Laredo -> Chicago from 0 to 63 runs"}],
    "cost_levers": [{"lever": "Cut deadhead", "impact_hint": "fuel is the biggest cost line"}],
    "entity_insights": {
        "state:TX": ["Add Laredo backhauls out of Texas."],
        "lane:Chicago,IL>Atlanta,GA": "Raise the quote another 3%.",
        "state:ZZ": ["invented key, must be dropped"],
    },
}


class Stub:
    kind = "gemini"
    model = "stub"

    def __init__(self, payload=None, text=None):
        self.calls: list[str] = []
        self.text = text if text is not None else json.dumps(payload if payload is not None else GOOD)

    async def generate_text(self, prompt):
        self.calls.append(prompt)
        return SimpleNamespace(status="ok", text=self.text, model="stub")


@pytest.fixture
async def sm():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(e, expire_on_commit=False)
    await seed(maker)
    yield maker
    await e.dispose()


async def _count(sm):
    async with sm() as s:
        return (await s.execute(select(func.count()).select_from(LaneInsightsCache))).scalar()


async def test_ok_parses_strict_schema_and_caches(sm):
    p = Stub()
    async with sm() as s:
        r = await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
    assert r.status == "ok" and not r.cached
    assert r.focus_lanes[0].delta_pct == 8.1 and r.declining_lanes[0].delta_pct == -11
    assert r.entity_insights["state:TX"] == ["Add Laredo backhauls out of Texas."]
    assert r.entity_insights["lane:Chicago,IL>Atlanta,GA"] == ["Raise the quote another 3%."]
    assert "state:ZZ" not in r.entity_insights
    assert await _count(sm) == 1


async def test_second_call_is_cached_and_makes_no_provider_call(sm):
    p = Stub()
    async with sm() as s:
        await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
    async with sm() as s:
        r = await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
    assert r.cached and r.status == "ok" and len(p.calls) == 1
    assert r.entity_insights["state:TX"]
    async with sm() as s:
        assert (await ai.get_cached_entity_insights(s, SCOPE, "year"))["state:TX"]


async def test_refresh_bypasses_cache(sm):
    p = Stub()
    async with sm() as s:
        await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
    async with sm() as s:
        r = await ai.get_lane_insights(s, SCOPE, "year", provider=p, refresh=True, now=NOW)
    assert not r.cached and len(p.calls) == 2 and await _count(sm) == 1


async def test_provider_null_is_unavailable_and_not_cached(sm):
    async with sm() as s:
        r = await ai.get_lane_insights(s, SCOPE, "year", provider=None, now=NOW)
        r2 = await ai.get_lane_insights(s, SCOPE, "year", provider=SimpleNamespace(kind="null"), now=NOW)
        r3 = await ai.get_lane_insights(s, SCOPE, "year", provider=None, resolve_error="resolve_failed:X", now=NOW)
    assert (r.status, r.ai_error) == ("unavailable", "provider_null")
    assert r2.ai_error == "provider_null" and r3.ai_error == "resolve_failed:X"
    assert r.focus_lanes == [] and r.entity_insights == {} and await _count(sm) == 0


@pytest.mark.parametrize("text", ["I think you should run more lanes.", "{not json", "[1,2]",
                                  json.dumps({"summary": "a paragraph"}),
                                  json.dumps({"focus_lanes": [], "declining_lanes": [], "market_shifts": [],
                                              "cost_levers": []})])
async def test_unparseable_never_invents(sm, text):
    async with sm() as s:
        r = await ai.get_lane_insights(s, SCOPE, "year", provider=Stub(text=text), now=NOW)
    assert (r.status, r.ai_error) == ("unavailable", "unparseable_response")
    assert r.focus_lanes == [] and await _count(sm) == 0


async def test_prompt_has_aggregates_only_inside_the_fence(sm):
    p = Stub()
    async with sm() as s:
        await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
    prompt = p.calls[0]
    assert FENCE_OPEN in prompt and FENCE_CLOSE in prompt
    assert "pickup_at" not in prompt and "broker_name" not in prompt  # no raw rows
    assert "lane:Chicago,IL>Atlanta,GA" in prompt and "state:TX" in prompt
    assert len(prompt) < 20_000


async def test_empty_tenant_is_empty_without_a_call(sm):
    p = Stub()
    async with sm() as s:
        r = await ai.get_lane_insights(s, H.Scope(tenant_id="01OTHER00000000000000000AA"), "year", provider=p, now=NOW)
    assert r.status == "empty" and p.calls == []


async def test_filtered_scope_gets_its_own_cache_row(sm):
    p = Stub()
    async with sm() as s:
        await ai.get_lane_insights(s, SCOPE, "year", provider=p, now=NOW)
        await ai.get_lane_insights(s, H.Scope(TENANT, state="TX"), "year", provider=p, now=NOW)
    assert await _count(sm) == 2
