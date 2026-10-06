"""Top-bar "today" counters — ``GET /analysis/topbar-counters``.

The top bar used to render a hardcoded baseline (18,240 scanned / 64 sent /
9 replies) that the client ticked up. These tests pin the replacement:

  * a fresh DB reads all zeros (no baseline anywhere),
  * only today-in-ET rows count (yesterday is excluded),
  * only the caller's tenant counts (fail-closed explicit filter),
  * test sends and our own outbound mail are not counted.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.analysis import kpi_service as kpi
from app.db import Base
from app.identity.models import Organization
from app.main import create_app
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
        # Seed OTHER organization so the pg16-strict FK (`leads.tenant_id`
        # → `organizations.id`) accepts rows that belong to it. On sqlite
        # this is a harmless extra insert.
        async with sm() as s:
            s.add(Organization(id=OTHER, slug="other-test", name="Other Co"))
            try:
                await s.commit()
            except Exception:  # noqa: BLE001 — swallow dup-insert from reuse
                await s.rollback()
        yield sm
    finally:
        await engine.dispose()


def _now_and_yesterday() -> tuple[datetime, datetime]:
    now = datetime.now(UTC)
    # one second before ET midnight is always "yesterday", whatever the clock says
    yesterday = kpi._snap_day_et(now) - timedelta(seconds=1)
    return now, yesterday


def _lead(lid: str, tenant: str, *, first: datetime, last: datetime) -> Lead:
    return Lead(id=lid, name=lid, kind="Broker", state="NJ", tenant_id=tenant, first_seen_at=first, last_seen_at=last)


def _sent(lead_id: str, tenant: str, at: datetime, *, is_test: bool = False) -> SentLog:
    return SentLog(
        lead_id=lead_id, tenant_id=tenant, mode="real", to_email="x@example.com", sent_at=at, is_test=is_test
    )


def _mail(mid: str, tenant: str, at: datetime, *, from_addr: str) -> MailMessage:
    return MailMessage(
        mailbox=MAILBOX,
        message_id=mid,
        thread_id=f"t-{mid}",
        history_id="1",
        from_addr=from_addr,
        sent_at=at,
        received_at=at,
        tenant_id=tenant,
    )


async def _seed(sm) -> None:
    now, yesterday = _now_and_yesterday()
    async with sm() as s:
        s.add_all(
            [
                # tenant: 2 seen today (1 of them new today), 1 seen only yesterday
                _lead("L-new", TENANT, first=now, last=now),
                _lead("L-old-rescanned", TENANT, first=now - timedelta(days=30), last=now),
                _lead("L-yesterday", TENANT, first=yesterday, last=yesterday),
                # other tenant: everything today, none of it may leak
                _lead("O-new", OTHER, first=now, last=now),
            ]
        )
        await s.flush()
        s.add_all(
            [
                _sent("L-new", TENANT, now),
                _sent("L-old-rescanned", TENANT, now),
                _sent("L-new", TENANT, now, is_test=True),  # test send — excluded
                _sent("L-new", TENANT, yesterday),  # yesterday — excluded
                _sent("O-new", OTHER, now),  # other tenant — excluded
                _mail("m1", TENANT, now, from_addr="broker@acme.com"),  # inbound today — counted
                _mail("m2", TENANT, now, from_addr=MAILBOX),  # our outbound — excluded
                _mail("m3", TENANT, yesterday, from_addr="broker@acme.com"),  # yesterday — excluded
                _mail("m4", OTHER, now, from_addr="broker@acme.com"),  # other tenant — excluded
            ]
        )
        await s.commit()


@pytest.mark.asyncio
async def test_fresh_db_is_all_zeros() -> None:
    async with _sessionmaker() as sm, sm() as s:
        out = await kpi.topbar_counters(s, TENANT)
    assert (out["scanned"], out["found"], out["sent"], out["replies"]) == (0, 0, 0, 0)
    assert out["today_et"] == kpi.today_et().isoformat()


@pytest.mark.asyncio
async def test_counts_today_et_and_tenant_only() -> None:
    async with _sessionmaker() as sm:
        await _seed(sm)
        async with sm() as s:
            out = await kpi.topbar_counters(s, TENANT)
    assert out["scanned"] == 2
    assert out["found"] == 1
    assert out["sent"] == 2
    assert out["replies"] == 1


@pytest.mark.asyncio
async def test_http_endpoint_returns_counters() -> None:
    async with _sessionmaker() as sm:
        app = create_app()
        app.state.sessionmaker = sm
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
            empty = await c.get("/analysis/topbar-counters")
            assert empty.status_code == 200, empty.text
            assert {k: empty.json()[k] for k in ("scanned", "found", "sent", "replies")} == {
                "scanned": 0,
                "found": 0,
                "sent": 0,
                "replies": 0,
            }
            await _seed(sm)
            r = await c.get("/analysis/topbar-counters")
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["scanned"], body["found"], body["sent"], body["replies"]) == (2, 1, 2, 1)
