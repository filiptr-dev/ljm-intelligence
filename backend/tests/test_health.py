"""Slice 1 acceptance: /health responds and reports DB connectivity against Neon."""

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import get_settings
from app.main import create_app


@pytest.mark.asyncio
async def test_health_reports_db_up() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True, f"health failed: {body}"
    assert body["db"] == "up"


def test_settings_load() -> None:
    s = get_settings()
    assert s.database_url.startswith("postgresql+psycopg://"), "DATABASE_URL not rewritten to psycopg driver"
    # Simulated delivery MUST default on unless explicitly turned off in .env.
    assert s.simulated_delivery is True
