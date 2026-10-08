"""HTTP-level tests for the freight-manager segment feed, composer-by-contact,
and campaign dry-run.

Run under both sqlite and the PG16 harness. The PG16 harness exercises the
partial unique index (``lead_contacts_lead_email_u``) via a direct second
insert — the dedupe backstop AC4 points at.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_app


async def _ensure_schema(app):
    """sqlite in-memory harness: create tables once before seeding."""
    from app.db import Base
    engine = app.state.engine
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    except Exception:
        # PG16 harness: tables already created by alembic; create_all is a no-op shim.
        pass


async def _seed(sm, lead_id: str, name: str, title: str, email: str | None = None):
    from datetime import datetime, timezone
    from app.prospecting.models import Lead, LeadContact

    async with sm() as s:
        from sqlalchemy import select
        existing_lead = (await s.execute(select(Lead).where(Lead.id == lead_id))).scalar_one_or_none()
        if existing_lead is None:
            s.add(Lead(id=lead_id, name=f"co-{lead_id}", kind="Shipper", state="NJ"))
            await s.flush()
        s.add(LeadContact(
            lead_id=lead_id, name=name, title=title, email=email,
            email_norm=(email.lower() if email else None),
            name_norm=name.lower(),
            source="site-scrape",
            discovered_at=datetime.now(timezone.utc),
        ))
        await s.commit()


@pytest.mark.asyncio
async def test_segment_lists_only_freight_managers_and_paginates() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        await _ensure_schema(app)
        sm = app.state.sessionmaker
        # Three freight-manager contacts across three leads + one Accountant.
        await _seed(sm, "MC-11", "Alice One", "Logistics Manager", "a@a.com")
        await _seed(sm, "MC-12", "Bob Two", "Shipping Manager", "b@b.com")
        await _seed(sm, "MC-13", "Carol Three", "VP Supply Chain", "c@c.com")
        await _seed(sm, "MC-14", "Dave Four", "Accountant", "d@d.com")

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/contacts", params={"role": "freight_manager", "limit": 2})
            assert r.status_code == 200
            body = r.json()
            assert len(body["items"]) == 2
            # Accountant excluded; all returned are freight managers.
            for item in body["items"]:
                assert item["is_freight_manager"] is True
            # Second page via cursor
            next_cur = body["next_cursor"]
            assert next_cur is not None
            r2 = await c.get("/contacts", params={"role": "freight_manager", "limit": 2, "cursor": next_cur})
            assert r2.status_code == 200
            body2 = r2.json()
            assert len(body2["items"]) == 1
            emails_seen = {i["email"] for i in body["items"]} | {i["email"] for i in body2["items"]}
            assert emails_seen == {"a@a.com", "b@b.com", "c@c.com"}


@pytest.mark.asyncio
async def test_lead_contacts_list_endpoint() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        await _ensure_schema(app)
        sm = app.state.sessionmaker
        await _seed(sm, "MC-21", "Alice", "Logistics Manager", "a@a.com")
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.get("/leads/MC-21/contacts")
            assert r.status_code == 200
            body = r.json()
            assert len(body["items"]) == 1
            assert body["items"][0]["email"] == "a@a.com"
            assert body["items"][0]["is_freight_manager"] is True


@pytest.mark.asyncio
async def test_draft_rejects_both_lead_id_and_contact_id() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        await _ensure_schema(app)
        sm = app.state.sessionmaker
        await _seed(sm, "MC-31", "Alice", "Logistics Manager", "a@a.com")
        from sqlalchemy import select
        from app.prospecting.models import LeadContact
        async with sm() as s:
            cid = (
                await s.execute(select(LeadContact).where(LeadContact.email == "a@a.com"))
            ).scalar_one().id

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/email/draft",
                json={"lead_id": "MC-31", "contact_id": cid, "tone": "professional"},
            )
            assert r.status_code == 422


@pytest.mark.asyncio
async def test_campaign_dry_run_returns_recipient_list_without_sending() -> None:
    app = create_app()
    async with app.router.lifespan_context(app):
        await _ensure_schema(app)
        sm = app.state.sessionmaker
        await _seed(sm, "MC-41", "Alice", "Logistics Manager", "a@a.com")
        await _seed(sm, "MC-42", "Bob", "Accountant", "b@b.com")  # excluded
        await _seed(sm, "MC-43", "Carol", "Shipping Manager", None)  # no email -> excluded

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/email/campaigns",
                json={"segment": "freight_manager", "tone": "professional", "limit": 50, "dry_run": True},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["dry_run"] is True
            assert body["enqueued"] == 0
            emails = {rec["email"] for rec in body["recipients"]}
            assert emails == {"a@a.com"}  # Accountant + no-email excluded
