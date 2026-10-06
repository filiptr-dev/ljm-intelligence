"""Campaign status — ``POST /analysis/campaign-status`` (plan 2026-10-06).

Backfills real sent / replied timestamps per recipient email so the
dashboard can stop lying about delivery. Opens and wins are never
returned — no table backs them.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import kpi_service as kpi
from app.db import Base
from app.identity.models import Organization
from app.models import Lead, MailMessage, SentLog
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId, set_tenant

TENANT = TenantId(LJM_TENANT_ID)
OTHER = TenantId("01OTHERTENANT000000000000B")
MAILBOX = "dispatch@ljm-demo.local"


@asynccontextmanager
async def _sessionmaker():
    set_tenant(TENANT)
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with sm() as s:
            s.add(Organization(id=OTHER, slug="other-test", name="Other Co"))
            try:
                await s.commit()
            except Exception:  # noqa: BLE001 — swallow dup-insert from reuse
                await s.rollback()
        yield sm
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_empty_emails_returns_empty() -> None:
    async with _sessionmaker() as sm, sm() as s:
        out = await kpi.campaign_status(s, TENANT, datetime.now(UTC) - timedelta(hours=1), [])
    assert out == {"items": []}


@pytest.mark.asyncio
async def test_sent_and_replied_backfill_in_window() -> None:
    now = datetime.now(UTC)
    created = now - timedelta(hours=2)
    before_campaign = created - timedelta(hours=1)

    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add(
                Lead(
                    id="L1", name="Acme", kind="Broker", state="NJ",
                    tenant_id=TENANT, first_seen_at=before_campaign, last_seen_at=before_campaign,
                )
            )
            await s.flush()
            s.add_all(
                [
                    # Before campaign — must NOT count
                    SentLog(
                        lead_id="L1", tenant_id=TENANT, mode="real",
                        to_email="ops@acme.com", sent_at=before_campaign,
                    ),
                    # In window — latest counts
                    SentLog(
                        lead_id="L1", tenant_id=TENANT, mode="real",
                        to_email="ops@acme.com", sent_at=now - timedelta(minutes=20),
                    ),
                    SentLog(
                        lead_id="L1", tenant_id=TENANT, mode="real",
                        to_email="ops@acme.com", sent_at=now - timedelta(minutes=30),
                    ),
                    # Test send — excluded
                    SentLog(
                        lead_id="L1", tenant_id=TENANT, mode="real",
                        to_email="ops@acme.com", sent_at=now - timedelta(minutes=10),
                        is_test=True,
                    ),
                    # Other tenant — excluded
                    SentLog(
                        lead_id="L1", tenant_id=OTHER, mode="real",
                        to_email="ops@acme.com", sent_at=now - timedelta(minutes=5),
                    ),
                    # Earliest inbound reply counts
                    MailMessage(
                        mailbox=MAILBOX, message_id="m1", thread_id="t1",
                        history_id="1", from_addr="ops@acme.com",
                        sent_at=now - timedelta(minutes=15), received_at=now - timedelta(minutes=15),
                        tenant_id=TENANT,
                    ),
                    MailMessage(
                        mailbox=MAILBOX, message_id="m2", thread_id="t1",
                        history_id="2", from_addr="ops@acme.com",
                        sent_at=now - timedelta(minutes=5), received_at=now - timedelta(minutes=5),
                        tenant_id=TENANT,
                    ),
                    # Outbound (from_addr == mailbox) — excluded
                    MailMessage(
                        mailbox=MAILBOX, message_id="m-out", thread_id="t1",
                        history_id="3", from_addr=MAILBOX,
                        sent_at=now - timedelta(minutes=10), received_at=now - timedelta(minutes=10),
                        tenant_id=TENANT,
                    ),
                    # Other tenant — excluded
                    MailMessage(
                        mailbox=MAILBOX, message_id="m-other", thread_id="t9",
                        history_id="4", from_addr="ops@acme.com",
                        sent_at=now - timedelta(minutes=1), received_at=now - timedelta(minutes=1),
                        tenant_id=OTHER,
                    ),
                ]
            )
            await s.commit()
        async with sm() as s:
            out = await kpi.campaign_status(
                s, TENANT, created, ["ops@acme.com", "noone@nowhere.com"]
            )

    items = {i["email"]: i for i in out["items"]}
    assert set(items) == {"ops@acme.com", "noone@nowhere.com"}
    assert items["ops@acme.com"]["sent_at"] is not None
    assert items["ops@acme.com"]["replied_at"] is not None
    assert items["noone@nowhere.com"]["sent_at"] is None
    assert items["noone@nowhere.com"]["replied_at"] is None
