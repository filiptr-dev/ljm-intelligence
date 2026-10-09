"""HTTP-level smoke for GET /analysis/lead/{lead_id} — must never 500.

Regression: the service's ``LeadSummaryResult`` dataclass defaulted
``risks`` to ``None``, which the pydantic ``LeadAiSummaryOut`` response
model (``risks: list[str]``, not Optional) rejected on every ``empty`` /
``unavailable`` result → 500 for every broker on prod. Lock the HTTP
contract so the route always answers 200 with a renderable envelope.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import Lead

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def client() -> AsyncClient:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    async with sm() as s:
        s.add(Lead(id="L1", name="Acme Brokerage", kind="Broker", state="NJ", primary_email="a@a.com"))
        await s.commit()
    app = create_app()
    app.state.sessionmaker = sm
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
    await engine.dispose()


async def test_lead_summary_empty_returns_200_renderable(client: AsyncClient):
    r = await client.get("/analysis/lead/L1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] in {"empty", "unavailable"}
    assert body["risks"] == []
    assert body["next_step"] is None


async def test_lead_summary_unknown_lead_is_404(client: AsyncClient):
    r = await client.get("/analysis/lead/does-not-exist")
    assert r.status_code == 404
