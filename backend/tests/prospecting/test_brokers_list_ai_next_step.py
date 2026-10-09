"""GET /brokers surfaces the cached AI ``next_step`` per row.

Seeds two broker leads — one with a cached ``LeadAiSummary.next_step`` and
one without — then calls the list endpoint and asserts the two row shapes
end-to-end through the router. No LLM is invoked (``provider=None``), which
is the whole point: the list route must never fan out to the model.

Mirrors the harness in ``tests/test_lead_ai_summary.py`` (in-memory SQLite +
ASGITransport) so the test is dialect-independent — the sqlite fallback
path exercises the same ``fetch_ai_next_steps`` lookup the PG hot path uses.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.deps import UserPrincipal, current_user
from app.db import Base
from app.main import create_app
from app.models import Lead, LeadAiSummary

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)

    app = create_app()
    app.state.sessionmaker = sm
    # Owner-only guard bypass — same pattern as test_brokers_api.
    app.dependency_overrides[current_user] = lambda: UserPrincipal(
        id="u1", email="owner@test", role="owner"
    )

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        c._sm = sm  # type: ignore[attr-defined]
        yield c

    await engine.dispose()


async def _seed_broker(sm, lid: str, name: str, state: str = "NJ") -> None:
    async with sm() as s:
        s.add(
            Lead(
                id=lid,
                name=name,
                kind="Broker",
                state=state,
                primary_email=f"ops@{lid.lower()}.test",
                phone="5551234567",
                raw={},
                evidence={},
                recommendations=[],
            )
        )
        await s.commit()


async def _seed_summary(
    sm, lead_id: str, next_step: dict | None, summary: str = "Strong partner."
) -> None:
    async with sm() as s:
        s.add(
            LeadAiSummary(
                lead_id=lead_id,
                summary=summary,
                input_hash="h1",
                model="stub",
                next_step=next_step,
            )
        )
        await s.commit()


async def test_list_surfaces_cached_next_step_without_calling_llm(
    client: AsyncClient,
) -> None:
    """AC1 + AC2: a broker with a cached ``next_step`` carries ``ai_next_step``
    and ``ai_summary_status="ready"``; a broker without any summary carries
    ``null`` and ``"none"``. The list route completes without invoking any
    AI provider — the fixture never wires one, so a stray call would raise.
    """
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_broker(sm, "L-with", "With AI")
    await _seed_broker(sm, "L-without", "Without AI")
    await _seed_summary(
        sm,
        "L-with",
        next_step={"label": "Call Jane", "detail": "She asked for a quote on PA→NJ."},
    )

    r = await client.get("/brokers")
    assert r.status_code == 200, r.text
    rows = {row["id"]: row for row in r.json()["items"]}
    assert set(rows) == {"L-with", "L-without"}

    with_ai = rows["L-with"]
    assert with_ai["ai_summary_status"] == "ready"
    assert with_ai["ai_next_step"] == {
        "label": "Call Jane",
        "detail": "She asked for a quote on PA→NJ.",
    }

    without_ai = rows["L-without"]
    assert without_ai["ai_summary_status"] == "none"
    assert without_ai["ai_next_step"] is None


async def test_list_legacy_summary_row_has_ready_but_null_next_step(
    client: AsyncClient,
) -> None:
    """A row that pre-dates migration 0033 (``next_step IS NULL``) still counts
    as ``"ready"`` — the UI renders a muted em-dash, no "Generate" button —
    because there is nothing new to summarise without more email signal.
    """
    sm = client._sm  # type: ignore[attr-defined]
    await _seed_broker(sm, "L-legacy", "Legacy AI")
    await _seed_summary(sm, "L-legacy", next_step=None)

    r = await client.get("/brokers")
    assert r.status_code == 200, r.text
    row = next(x for x in r.json()["items"] if x["id"] == "L-legacy")
    assert row["ai_summary_status"] == "ready"
    assert row["ai_next_step"] is None
