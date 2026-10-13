"""Pure tests for app.inbox.import_mailboxes — ladder + planner + mailbox
resolution. Does not hit real Gmail; uses an in-memory sqlite + the
``SimulatedMailbox`` adapter.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — register tables
from app.db import Base
from app.inbox.import_mailboxes import (
    LADDER_HOURS,
    _next_step_hours,
    _plan_mailbox,
    _resolve_mailboxes,
)
from app.inbox.models import MailCursor
from app.integrations.adapters.email.credentials import (
    reset_vault_cache,
    set_vault_allowlist_cache,
)


async def _sm() -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


def test_next_step_hours_walks_ladder() -> None:
    assert _next_step_hours(0) == LADDER_HOURS[0]
    assert _next_step_hours(2) == 12
    assert _next_step_hours(23.99) == 24
    assert _next_step_hours(720) == 2160
    assert _next_step_hours(2160) is None
    assert _next_step_hours(10_000) is None


@pytest.mark.asyncio
async def test_plan_hours_without_cursor_yields_window() -> None:
    sm = await _sm()
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    plan = await _plan_mailbox(sm, "a@x.com", hours=2, expand=False, now=now)
    assert plan is not None
    assert plan.older_than == now - timedelta(hours=2)
    assert plan.newer_than == now
    assert abs(plan.width_hours - 2.0) < 1e-6


@pytest.mark.asyncio
async def test_plan_hours_skips_already_covered() -> None:
    sm = await _sm()
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    # Cursor already reaches further back than the 2h window asks for.
    async with sm() as s:
        s.add(MailCursor(
            mailbox="a@x.com",
            history_id="1",
            backfilled_through_at=now - timedelta(hours=48),
        ))
        await s.commit()
    plan = await _plan_mailbox(sm, "a@x.com", hours=2, expand=False, now=now)
    assert plan is None


@pytest.mark.asyncio
async def test_plan_expand_picks_next_ladder_rung() -> None:
    sm = await _sm()
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    # Covered 2h back → next step is 12h; walk the gap (12h..2h).
    async with sm() as s:
        s.add(MailCursor(
            mailbox="a@x.com",
            history_id="1",
            backfilled_through_at=now - timedelta(hours=2),
        ))
        await s.commit()
    plan = await _plan_mailbox(sm, "a@x.com", hours=None, expand=True, now=now)
    assert plan is not None
    assert plan.older_than == now - timedelta(hours=12)
    assert plan.newer_than == now - timedelta(hours=2)


@pytest.mark.asyncio
async def test_plan_expand_from_empty_starts_at_two_hours() -> None:
    sm = await _sm()
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    plan = await _plan_mailbox(sm, "a@x.com", hours=None, expand=True, now=now)
    assert plan is not None
    assert plan.older_than == now - timedelta(hours=2)
    assert plan.newer_than == now


@pytest.mark.asyncio
async def test_plan_expand_at_top_returns_none() -> None:
    sm = await _sm()
    now = datetime(2026, 10, 13, 12, 0, tzinfo=UTC)
    async with sm() as s:
        s.add(MailCursor(
            mailbox="a@x.com",
            history_id="1",
            backfilled_through_at=now - timedelta(hours=LADDER_HOURS[-1] + 1),
        ))
        await s.commit()
    plan = await _plan_mailbox(sm, "a@x.com", hours=None, expand=True, now=now)
    assert plan is None


def test_resolve_mailboxes_honours_allowlist() -> None:
    reset_vault_cache()
    try:
        set_vault_allowlist_cache(["a@x.com", "b@x.com"])

        class S:  # settings stub — only the two attrs resolve_* touches.
            mail_read_allowlist = ""

        s = S()
        assert _resolve_mailboxes(s, None) == ["a@x.com", "b@x.com"]
        assert _resolve_mailboxes(s, ["a@x.com", "c@x.com"]) == ["a@x.com"]
        # Nothing allowlisted at all → refuse to run.
        set_vault_allowlist_cache(None)
        assert _resolve_mailboxes(s, None) == []
    finally:
        reset_vault_cache()
