"""Live feed — ``GET /analysis/live-feed`` (plan 2026-10-06).

Replaces the client-side ``createRng`` fiction. Three real kinds only:
``found`` / ``outreach`` / ``reply``. Rolling 12-hour window; tenant
filter explicit on every leg (fail closed; sqlite has no RLS).
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
        # Seed OTHER organization so FK-strict pg16 harness accepts rows that
        # belong to it. Both tenants' orgs must exist before any TenantMixin
        # row insert — see projects/.../2026-10-01-pg-test-harness-real-fks.md.
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
async def test_feed_tenant_scoped_and_12h_windowed() -> None:
    now = datetime.now(UTC)
    recent = now - timedelta(minutes=30)
    older_recent = now - timedelta(hours=11)
    stale = now - timedelta(hours=13)

    async with _sessionmaker() as sm:
        async with sm() as s:
            s.add_all(
                [
                    # In-window, this tenant — must appear
                    Lead(
                        id="L-recent", name="Recent Freight", kind="Broker", state="NJ",
                        tenant_id=TENANT, first_seen_at=recent, last_seen_at=recent,
                    ),
                    Lead(
                        id="L-older", name="Older Freight", kind="Shipper", state="PA",
                        tenant_id=TENANT, first_seen_at=older_recent, last_seen_at=older_recent,
                    ),
                    # 13h ago — out of window, must NOT appear
                    Lead(
                        id="L-stale", name="Stale Freight", kind="Broker", state="NY",
                        tenant_id=TENANT, first_seen_at=stale, last_seen_at=stale,
                    ),
                    # Other tenant — must NOT appear
                    Lead(
                        id="L-other", name="Other Tenant Freight", kind="Broker", state="CA",
                        tenant_id=OTHER, first_seen_at=recent, last_seen_at=recent,
                    ),
                ]
            )
            await s.flush()
            s.add_all(
                [
                    SentLog(
                        lead_id="L-recent", tenant_id=TENANT, mode="real",
                        to_email="ops@acme.com", subject="Capacity today", sent_at=recent,
                    ),
                    # test send — excluded
                    SentLog(
                        lead_id="L-recent", tenant_id=TENANT, mode="real",
                        to_email="x@example.com", sent_at=recent, is_test=True,
                    ),
                    # other tenant — excluded
                    SentLog(
                        lead_id="L-other", tenant_id=OTHER, mode="real",
                        to_email="o@example.com", sent_at=recent,
                    ),
                    MailMessage(
                        mailbox=MAILBOX, message_id="m-in", thread_id="t1",
                        history_id="1", from_addr="broker@acme.com",
                        subject="Re: Capacity today", sent_at=recent, received_at=recent,
                        tenant_id=TENANT,
                    ),
                    # outbound (from_addr == mailbox) — excluded
                    MailMessage(
                        mailbox=MAILBOX, message_id="m-out", thread_id="t2",
                        history_id="2", from_addr=MAILBOX,
                        subject="Capacity", sent_at=recent, received_at=recent,
                        tenant_id=TENANT,
                    ),
                    # other tenant — excluded
                    MailMessage(
                        mailbox=MAILBOX, message_id="m-other", thread_id="t3",
                        history_id="3", from_addr="x@foo.com",
                        subject="Re: hi", sent_at=recent, received_at=recent,
                        tenant_id=OTHER,
                    ),
                ]
            )
            await s.commit()
        async with sm() as s:
            out = await kpi.live_feed(s, TENANT, limit=25)

    items = out["items"]
    ids = {i["id"] for i in items}
    kinds = {i["kind"] for i in items}

    assert "found:L-recent" in ids
    assert "found:L-older" in ids
    assert "outreach:1" in ids or any(i["id"].startswith("outreach:") for i in items)
    assert "reply:m-in" in ids
    # Tenant isolation
    assert "found:L-other" not in ids
    assert "reply:m-other" not in ids
    # Window
    assert "found:L-stale" not in ids
    # Kinds — only the three real ones
    assert kinds <= {"found", "outreach", "reply"}
    # Ordered by at desc
    ats = [i["at"] for i in items]
    assert ats == sorted(ats, reverse=True)


@pytest.mark.asyncio
async def test_fresh_db_is_empty() -> None:
    async with _sessionmaker() as sm, sm() as s:
        out = await kpi.live_feed(s, TENANT, limit=25)
    assert out == {"items": []}
