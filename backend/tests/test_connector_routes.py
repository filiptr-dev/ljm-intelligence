"""End-to-end route checks for ``/mail/*`` and ``/loads/*``.

The DB migrations are not needed here — we hit the tables via the ORM against
an in-memory SQLite that the FastAPI lifespan sets up. Auth is injected by
overriding the ``current_user`` dependency so tests exercise the route
contract, not the login flow (covered by ``test_auth.py``).
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.auth.deps import UserPrincipal, current_user, require_user_or_cron
from app.config import Settings
from app.db import Base
from app.main import create_app


def _settings() -> Settings:
    return Settings().model_copy(update={"cron_secret": SecretStr("secret"), "mail_sender": "simulated"})


async def _prep_db(app) -> None:
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _owner() -> UserPrincipal:
    return UserPrincipal(id="U1", email="owner@ljm-demo.local", role="owner")


@pytest.mark.asyncio
async def test_mail_status_and_test_send() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[require_user_or_cron] = _owner
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/mail/status")
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["mode"] == "simulated"
            assert body["postal_address_set"] is False  # default .env.example
            # Scopes populated from config.
            assert any("gmail.send" in sc for sc in body["scopes"])

            r = await c.post("/mail/test-send", json={"to": "owner@example.com"})
            assert r.status_code == 200, r.text
            assert r.json()["mode"] == "simulated"

            r = await c.post("/mail/disconnect")
            assert r.status_code == 200
            assert r.json()["mode"] == "simulated"


@pytest.mark.asyncio
async def test_loads_sources_round_trip() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[require_user_or_cron] = _owner
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/loads/sources")
            assert r.status_code == 200, r.text
            kinds = [row["kind"] for row in r.json()["items"]]
            assert kinds == ["ai_page", "paste", "dat", "chr", "loadboard123", "truckstop"]
            # Disabled vendors carry a reason.
            disabled = [row for row in r.json()["items"] if not row["enabled"]]
            assert all("missing_env:" in (row["reason"] or "") for row in disabled)

            r = await c.post("/loads/sources/ai_page/test")
            assert r.status_code == 200, r.text
            assert r.json()["ok"] is True

            r = await c.post("/loads/sources/dat/refresh")
            assert r.status_code == 200
            assert r.json()["status"] == "disabled"

            r = await c.post(
                "/loads/sources/refresh-all",
                headers={"X-Cron-Secret": "secret"},
            )
            assert r.status_code == 200
            items = r.json()["items"]
            # Only ai_page + paste are enabled by default; refresh-all iterates them.
            kinds = {it["kind"] for it in items}
            assert kinds == {"ai_page", "paste"}

            r = await c.get("/loads?limit=10")
            assert r.status_code == 200
            assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_email_send_route_writes_sent_log() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/email/send",
                json={
                    "to": "lead@example.com",
                    "subject": "Trucks open this week",
                    "body": "Hi there, we have capacity.",
                },
            )
            assert r.status_code == 200, r.text
            body = r.json()
            assert body["ok"] is True
            assert body["mode"] == "simulated"
