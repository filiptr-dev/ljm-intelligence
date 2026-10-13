"""freight_runs ingest — LoadStop grid + inbox rate-con promotion.

Narrow, in-memory. Mocks the LoadStop paged client; writes real rows
through the real ORM against sqlite so the dedupe + upsert contract is
exercised end-to-end.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.analysis.ingest_freight_runs import (
    ingest_inbox_runs,
    ingest_loadstop_runs,
)
from app.analysis.models import FreightRun
from app.db import Base
from app.fleet.models import Truck  # noqa: F401 — register FK target in Base.metadata
from app.prospecting.models import (
    Lead,  # noqa: F401 — register FK target
    Load,
)
from app.rates.models import DieselPrice

pytestmark = pytest.mark.asyncio

TENANT = "01LJMORGLJM00000000000000A"


class _FakeClient:
    """Minimal stand-in for the LoadStop paged client. Yields the
    rows the test gave it in one page, keyed on ``(service, method)``."""

    def __init__(self, feeds: dict[tuple[str, str], list[dict]]):
        self.feeds = feeds

    async def paged(self, service, method, *, page_size=200, extra=None, result_path=None):
        yield list(self.feeds.get((service, method), []))


@pytest.fixture
async def sm():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(e, expire_on_commit=False)
    # Seed one diesel row so fuel estimate has a price.
    async with maker() as s:
        s.add(DieselPrice(padd="US", week_of=datetime(2026, 10, 7).date(), price_usd=Decimal("3.80")))
        await s.commit()
    yield maker
    await e.dispose()


def _splitload_row(sid: int, *, pickup: str = "2026-09-16T00:00:00", o=("Buffalo", "NY"),
                   d=("Charleston", "WV"), rate: float | None = 2500.0, miles: int | None = 400) -> dict:
    return {
        "id": sid, "pickupDate": pickup, "deliveryDate": pickup,
        "pickupCity": o[0], "pickupState": o[1],
        "deliveryCity": d[0], "deliveryState": d[1],
        "customerName": "Landstar", "vanType": 1,
        "totalAmount": rate, "calculatedTotalMiles": miles,
        "loadStatus": 2,
    }


async def test_loadstop_upsert_is_idempotent(sm):
    """Two runs over the same page insert once, then update — the
    unique anchor is ``(tenant_id, source, source_ref)``."""
    client = _FakeClient({
        ("splitLoads", "GetAllWithSP"): [_splitload_row(1, rate=2500), _splitload_row(2)],
        ("loads", "GetAllWithSP"): [_splitload_row(1, rate=3000)],  # overlaps on source_ref? no — different subsource
    })
    async with sm() as s:
        first = await ingest_loadstop_runs(s, client, tenant_id=TENANT)
        await s.commit()
    async with sm() as s:
        again = await ingest_loadstop_runs(s, client, tenant_id=TENANT)
        await s.commit()
        total = (await s.execute(select(func.count()).select_from(FreightRun))).scalar_one()
    assert first["inserted"] == 3
    assert first["updated"] == 0
    assert again["inserted"] == 0
    assert again["updated"] == 3
    assert total == 3  # splitLoads:1, splitLoads:2, loads:1


async def test_loadstop_skips_out_of_area_and_missing_lane(sm):
    client = _FakeClient({
        ("splitLoads", "GetAllWithSP"): [
            _splitload_row(1, o=("Dallas", "TX")),  # TX = out of area
            _splitload_row(2, d=("", "")),  # no dest
            _splitload_row(3),  # keeper
        ],
        ("loads", "GetAllWithSP"): [],
    })
    async with sm() as s:
        totals = await ingest_loadstop_runs(s, client, tenant_id=TENANT)
        await s.commit()
    assert totals["inserted"] == 1
    assert totals["skipped"] == 2


async def test_loadstop_months_cutoff(sm):
    old = (datetime.now(UTC) - timedelta(days=400)).isoformat()
    recent = (datetime.now(UTC) - timedelta(days=10)).isoformat()
    client = _FakeClient({
        ("splitLoads", "GetAllWithSP"): [
            _splitload_row(1, pickup=old),
            _splitload_row(2, pickup=recent),
        ],
        ("loads", "GetAllWithSP"): [],
    })
    async with sm() as s:
        totals = await ingest_loadstop_runs(s, client, tenant_id=TENANT, months=6)
        await s.commit()
    assert totals["inserted"] == 1
    assert totals["skipped"] == 1


async def test_loadstop_sparse_row_keeps_null_cost_and_revenue(sm):
    """A trip with no rate + no miles lands with NULL revenue + NULL
    fuel — never a fabricated 0."""
    client = _FakeClient({
        ("splitLoads", "GetAllWithSP"): [_splitload_row(1, rate=None, miles=None)],
        ("loads", "GetAllWithSP"): [],
    })
    async with sm() as s:
        await ingest_loadstop_runs(s, client, tenant_id=TENANT)
        await s.commit()
        run = (await s.execute(select(FreightRun))).scalar_one()
    assert run.revenue_usd is None
    assert run.cost_fuel_usd is None
    assert run.cost_driver_usd is None
    assert run.cost_load_usd is None
    assert run.cost_dispatch_usd is None
    assert run.miles is None


async def test_inbox_promotes_rows_with_lane_and_rate(sm):
    """An inbox ``loads`` row with derived lane + rate becomes an
    ``source='inbox'`` run. Lane-only / rate-only rows are skipped."""
    async with sm() as s:
        s.add_all([
            Load(
                tenant_id=TENANT, source="inbox", source_ref="m:1", broker_name="Acme",
                derived_origin="Buffalo, NY", derived_dest="Charleston, WV",
                derived_rate_usd=Decimal("2500.00"), miles=400,
                pickup_date=datetime(2026, 9, 20, tzinfo=UTC),
                posted_at=datetime(2026, 9, 20, tzinfo=UTC),
            ),
            Load(  # lane only — skipped
                tenant_id=TENANT, source="inbox", source_ref="m:2", broker_name="Acme",
                derived_origin="Buffalo, NY", derived_dest="Charleston, WV",
                pickup_date=datetime(2026, 9, 20, tzinfo=UTC),
            ),
            Load(  # rate only — skipped
                tenant_id=TENANT, source="inbox", source_ref="m:3", broker_name="Acme",
                derived_rate_usd=Decimal("1000.00"),
                pickup_date=datetime(2026, 9, 20, tzinfo=UTC),
            ),
        ])
        await s.commit()

    async with sm() as s:
        totals = await ingest_inbox_runs(s, tenant_id=TENANT)
        await s.commit()
        run = (await s.execute(select(FreightRun))).scalar_one()
    assert totals["inserted"] == 1
    # Rows missing lane or rate are filtered out by the SQL WHERE
    # itself (cheaper than loading them to then skip), so ``rows_in``
    # reflects only the eligible candidate row.
    assert totals["rows_in"] == 1
    assert run.source == "inbox"
    assert run.source_ref == "inbox:1" or run.source_ref.endswith(":1")  # by load.id
    assert float(run.revenue_usd) == 2500.0


async def test_inbox_skips_duplicate_of_loadstop_trip(sm):
    """A rate-con that lands on the same lane + pickup day as a
    LoadStop trip is skipped — the LoadStop row is the authoritative
    one; the rate-con was the pre-image."""
    client = _FakeClient({
        ("splitLoads", "GetAllWithSP"): [
            _splitload_row(1, pickup="2026-09-20T00:00:00"),
        ],
        ("loads", "GetAllWithSP"): [],
    })
    async with sm() as s:
        await ingest_loadstop_runs(s, client, tenant_id=TENANT)
        s.add(Load(
            tenant_id=TENANT, source="inbox", source_ref="m:9",
            derived_origin="Buffalo, NY", derived_dest="Charleston, WV",
            derived_rate_usd=Decimal("2500.00"),
            pickup_date=datetime(2026, 9, 20, tzinfo=UTC),
        ))
        await s.commit()
    async with sm() as s:
        totals = await ingest_inbox_runs(s, tenant_id=TENANT)
        await s.commit()
    assert totals["skipped"] == 1
    assert totals["inserted"] == 0
