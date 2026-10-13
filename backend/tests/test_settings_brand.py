"""Settings - owner-editable email signature (brand_* fields). Offline: sqlite in-memory."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from tests.test_settings_unsub import _hermetic_settings


@pytest.fixture
async def client():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app = create_app()
    app.state.sessionmaker = async_sessionmaker(e, expire_on_commit=False)
    app.state.settings = _hermetic_settings(outreach_from_email="contact@ljminternational.com")
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        yield c
    await e.dispose()


@pytest.mark.asyncio
async def test_signature_defaults_empty_and_sender_email_exposed(client):
    body = (await client.get("/settings")).json()
    assert [body[k] for k in ("brand_dispatcher", "brand_company", "brand_phone", "brand_email")] == ["", "", "", ""]
    assert body["sender_email"] == "contact@ljminternational.com"


@pytest.mark.asyncio
async def test_signature_roundtrip_and_strip(client):
    r = await client.put("/settings", json={"brand_dispatcher": "  Pat  ", "brand_phone": "+1 312", "brand_email": "pat@ljm.test"})
    assert r.status_code == 200, r.text
    body = (await client.get("/settings")).json()
    assert body["brand_dispatcher"] == "Pat"
    assert body["brand_phone"] == "+1 312"
    assert body["brand_email"] == "pat@ljm.test"


@pytest.mark.asyncio
async def test_signature_validation(client):
    assert (await client.put("/settings", json={"brand_email": "nope"})).status_code == 422
    assert (await client.put("/settings", json={"brand_dispatcher": "x" * 129})).status_code == 422
    assert (await client.put("/settings", json={"brand_email": ""})).status_code == 200
