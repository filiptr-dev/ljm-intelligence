"""Cursor must not claim coverage of a window that failed part-way."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401
from app.db import Base
from app.inbox.models import MailCursor
from app.integrations.adapters.email import ingest


@pytest.mark.asyncio
async def test_mid_window_failure_does_not_advance_cursor(monkeypatch) -> None:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def fake_upsert(session, msg):
        return True

    monkeypatch.setattr(ingest, "_upsert", fake_upsert)
    monkeypatch.setattr(ingest, "BACKFILL_COMMIT_BATCH", 2)

    class Msg:
        def __init__(self, i: int) -> None:
            self.history_id = str(100 + i)

    class Src:
        async def backfill(self, mailbox, since, *, window=None):
            for i in range(5):
                yield Msg(i)
            raise RuntimeError("gmail 500")

    now = datetime.now(UTC)
    async with sm() as s:
        stats = await ingest.ingest_backfill(
            s, Src(), "a@x.com", window=(now - timedelta(hours=12), now)
        )
    assert stats.status == "error"
    async with sm() as s:
        cur = (await s.execute(select(MailCursor))).scalar_one_or_none()
    assert cur is None or cur.backfilled_through_at is None
