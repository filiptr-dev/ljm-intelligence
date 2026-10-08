"""mail_backfill → one-shot analysis.nightly trigger.

ACs covered:

* AC2 — First `mail_backfill` for a tenant enqueues `analysis.nightly`
  exactly once; a second run short-circuits.
* AC3 — Tenant isolation: a tenant that already has a `PredictionRun`
  does not re-trigger, and triggers are keyed on the correct tenant.
* AC4 — If `svc_backfill` raises, the gate query never runs and
  `dispatch` is never called.

The tests mock `svc_backfill` (no Gmail) and patch `dispatch` on the
`app.inbox.jobs` module so we can assert call-count + args without hitting
procrastinate. The real DB (sqlite in-memory via conftest) is used for the
PredictionRun gate query.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from app.analysis.models import PredictionRun
from app.inbox import jobs as inbox_jobs
from app.integrations.mail_service import IngestStatsRow


def _stats(mailbox: str) -> IngestStatsRow:
    return IngestStatsRow(
        mailbox=mailbox, read=0, upserted=0, skipped=0,
        last_history_id=None, status="ok", error=None,
    )


@pytest.fixture
def patch_backfill_ok(monkeypatch):
    """svc_backfill returns a benign stats row."""
    async def _ok(sm, settings, *, mailbox, months):
        return _stats(mailbox)
    monkeypatch.setattr("app.integrations.mail_service.backfill", _ok)


@pytest.fixture
def patch_dispatch(monkeypatch):
    mock = AsyncMock(return_value=1)
    # `dispatch` is imported inside the job body (`from app.shared.queue
    # import dispatch`), so we patch at the source module.
    monkeypatch.setattr("app.shared.queue.dispatch", mock)
    return mock


async def _seed_prediction_run(sessionmaker, tenant_id: str) -> None:
    async with sessionmaker() as session:
        session.add(PredictionRun(
            tenant_id=tenant_id,
            kind="analysis_nightly",
            started_at=datetime.now(UTC),
            status="done",
        ))
        await session.commit()


@pytest.mark.asyncio
async def test_first_backfill_triggers_analysis_exactly_once(
    patch_backfill_ok, patch_dispatch
):
    """AC2 — zero prior runs → one dispatch; second call → no new dispatch."""
    tenant = "demo-tenant"

    await inbox_jobs.mail_backfill(tenant_id=tenant, mailbox="a@x.com", months=1)
    assert patch_dispatch.call_count == 1
    args, kwargs = patch_dispatch.call_args
    assert args[0] == "analysis.nightly"
    assert kwargs == {"tenant_id": tenant}

    # Simulate the nightly run completing by writing a PredictionRun row — the
    # next backfill should short-circuit on the gate.
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker
    engine = create_engine(get_settings())
    sm = create_sessionmaker(engine)
    try:
        await _seed_prediction_run(sm, tenant)
    finally:
        await engine.dispose()

    await inbox_jobs.mail_backfill(tenant_id=tenant, mailbox="a@x.com", months=1)
    assert patch_dispatch.call_count == 1  # unchanged — gate held


@pytest.mark.asyncio
async def test_tenant_isolation(patch_backfill_ok, patch_dispatch):
    """AC3 — a prior run for tenant B does not suppress a trigger for tenant A."""
    from app.config import get_settings
    from app.db import create_engine, create_sessionmaker

    engine = create_engine(get_settings())
    sm = create_sessionmaker(engine)
    try:
        await _seed_prediction_run(sm, "tenant-b")
    finally:
        await engine.dispose()

    # A has zero runs → dispatch fires once with A's id.
    await inbox_jobs.mail_backfill(tenant_id="tenant-a", mailbox="a@x.com", months=1)
    assert patch_dispatch.call_count == 1
    assert patch_dispatch.call_args.kwargs == {"tenant_id": "tenant-a"}

    # B already has a run → no additional dispatch.
    await inbox_jobs.mail_backfill(tenant_id="tenant-b", mailbox="b@x.com", months=1)
    assert patch_dispatch.call_count == 1


@pytest.mark.asyncio
async def test_backfill_failure_never_triggers_analysis(monkeypatch, patch_dispatch):
    """AC4 — svc_backfill raises → gate + dispatch never executed."""
    async def _raise(sm, settings, *, mailbox, months):
        raise RuntimeError("gmail down")
    monkeypatch.setattr("app.integrations.mail_service.backfill", _raise)

    with pytest.raises(RuntimeError, match="gmail down"):
        await inbox_jobs.mail_backfill(tenant_id="any", mailbox="a@x.com", months=1)

    assert patch_dispatch.call_count == 0
