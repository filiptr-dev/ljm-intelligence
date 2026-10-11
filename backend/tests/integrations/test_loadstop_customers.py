"""Customers + contacts ingest — match-first, write-if-null, no clobber."""

from __future__ import annotations

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.integrations.adapters.loadboard.loadstop.fakes import FakeAbpClient
from app.integrations.adapters.loadboard.loadstop.ingest.customers import (
    ingest_contacts,
    ingest_customers,
)
from app.prospecting.models import Lead, LeadContact
from app.shared.orm import LJM_TENANT_ID


SAMPLE_CUSTOMERS = [
    {
        "id": "ls-c-1",
        "name": "Acme Freight LLC",
        "mcNumber": "MC-123456",
        "dotNumber": "7654321",
        "type": "Broker",
        "phone": "555-0100",
        "email": "ops@acmefreight.invented",
        "address": {"city": "Atlanta", "state": "GA", "line1": "1 Peachtree"},
        "creditStatus": "Good",
    },
    {
        "id": "ls-c-2",
        "name": "Rivergate Logistics",
        "mcNumber": "999001",
        "type": "Broker",
        "phone": "555-0200",
        "address": {"city": "Nashville", "state": "TN"},
    },
]


@pytest.fixture
async def db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    yield maker
    await engine.dispose()


async def test_customers_creates_new_leads_and_stamps_loadstop(db):
    client = FakeAbpClient({("customers", "GetAll"): [SAMPLE_CUSTOMERS]})
    async with db() as s:
        counts = await ingest_customers(s, client)
        await s.commit()
    assert counts["rows_in"] == 2
    assert counts["created"] == 2
    assert counts["matched"] == 0
    async with db() as s:
        leads = (await s.execute(select(Lead).where(Lead.tenant_id == LJM_TENANT_ID))).scalars().all()
    assert len(leads) == 2
    acme = next(x for x in leads if x.name == "Acme Freight LLC")
    assert acme.mc == "123456"  # cleaned
    assert acme.raw["loadstop"]["customer_id"] == "ls-c-1"
    assert acme.raw["loadstop"]["credit_status"] == "Good"
    assert acme.raw["review_needed"] is True


async def test_customers_matches_on_mc_and_writes_if_null(db):
    """Pre-seed a lead with the same MC; the import fills missing fields but never clobbers."""
    async with db() as s:
        pre = Lead(
            tenant_id=LJM_TENANT_ID,
            id="MC-123456",
            mc="123456",
            name="Acme Freight (operator-edited)",
            kind="Broker",
            state="GA",
            raw={},
        )
        s.add(pre)
        await s.commit()
    client = FakeAbpClient({("customers", "GetAll"): [[SAMPLE_CUSTOMERS[0]]]})
    async with db() as s:
        counts = await ingest_customers(s, client)
        await s.commit()
    assert counts["matched"] == 1
    assert counts["created"] == 0
    async with db() as s:
        hit = (await s.execute(select(Lead).where(Lead.id == "MC-123456"))).scalar_one()
    assert hit.name == "Acme Freight (operator-edited)"  # not clobbered
    assert hit.phone == "555-0100"  # was null → filled
    assert hit.primary_email == "ops@acmefreight.invented"
    assert hit.raw["loadstop"]["customer_id"] == "ls-c-1"


async def test_contacts_attaches_to_lead_by_customer_id(db):
    client1 = FakeAbpClient({("customers", "GetAll"): [[SAMPLE_CUSTOMERS[0]]]})
    async with db() as s:
        await ingest_customers(s, client1)
        await s.commit()
    client2 = FakeAbpClient(
        {
            ("contacts", "GetAll"): [
                [
                    {"id": "ct-1", "customerId": "ls-c-1", "name": "Jane Doe", "email": "jane@acmefreight.invented", "phone": "555-0101", "title": "Dispatch"},
                    {"id": "ct-2", "customerId": "ls-no-match", "name": "Nobody", "email": "x@y.invented"},
                ]
            ]
        }
    )
    async with db() as s:
        counts = await ingest_contacts(s, client2)
        await s.commit()
    assert counts["rows_in"] == 2
    assert counts["rows_upserted"] == 1  # the no-match row drops
    async with db() as s:
        contacts = (await s.execute(select(LeadContact))).scalars().all()
    assert len(contacts) == 1
    assert contacts[0].email == "jane@acmefreight.invented"
    assert contacts[0].title == "Dispatch"
