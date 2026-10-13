"""One-off backfill: pull LoadStop history + inbox rate-cons into
``freight_runs``.

Usage:

    # dry run — count what would land, write nothing
    python -m app.analysis.backfill_freight_runs --dry-run --months 24

    # commit for the LJM tenant, last 12 months
    python -m app.analysis.backfill_freight_runs --months 12

Teaching note: ``--dry-run`` wraps the whole batch in a
``session.rollback()`` instead of a ``begin()`` dance — we let the
ingest write rows, then discard the transaction. Everything else sees
the same code path as the real run, so a dry-run can't lie about what
the real run would do.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging

from sqlalchemy import func, select

from app.analysis.ingest_freight_runs import backfill_runs
from app.analysis.models import FreightRun
from app.config import get_settings
from app.db import create_engine, create_sessionmaker

# Explicit imports pin the ``trucks`` / ``leads`` tables into
# ``Base.metadata`` before FreightRun's FKs resolve — a standalone CLI
# doesn't walk ``app.main`` the way the FastAPI boot does.
from app.fleet.models import Truck  # noqa: F401
from app.integrations.adapters.loadboard.loadstop.session import open_client
from app.prospecting.models import Lead  # noqa: F401
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId, set_tenant

LOG = logging.getLogger("backfill_freight_runs")


async def _snapshot(session, tenant_id: str) -> dict:
    """Count rows + list top-5 lanes by run count for the report."""
    total = (
        await session.execute(
            select(func.count()).select_from(FreightRun).where(FreightRun.tenant_id == tenant_id)
        )
    ).scalar_one()
    with_rev = (
        await session.execute(
            select(func.count()).select_from(FreightRun).where(
                FreightRun.tenant_id == tenant_id,
                FreightRun.revenue_usd.is_not(None),
            )
        )
    ).scalar_one()
    pickup_range = (
        await session.execute(
            select(func.min(FreightRun.pickup_at), func.max(FreightRun.pickup_at)).where(
                FreightRun.tenant_id == tenant_id
            )
        )
    ).first()
    top = (
        await session.execute(
            select(
                FreightRun.origin_city, FreightRun.origin_state,
                FreightRun.dest_city, FreightRun.dest_state,
                func.count().label("runs"),
            )
            .where(FreightRun.tenant_id == tenant_id)
            .group_by(
                FreightRun.origin_city, FreightRun.origin_state,
                FreightRun.dest_city, FreightRun.dest_state,
            )
            .order_by(func.count().desc())
            .limit(5)
        )
    ).all()
    return {
        "total_runs": total,
        "with_revenue": with_rev,
        "pickup_min": pickup_range[0].isoformat() if pickup_range and pickup_range[0] else None,
        "pickup_max": pickup_range[1].isoformat() if pickup_range and pickup_range[1] else None,
        "top_lanes": [
            {"origin": f"{o_c}, {o_s}", "dest": f"{d_c}, {d_s}", "runs": n}
            for (o_c, o_s, d_c, d_s, n) in top
        ],
    }


async def _run(args: argparse.Namespace) -> int:
    set_tenant(TenantId(args.tenant))
    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with open_client(sm) as client, sm() as session:
            before = await _snapshot(session, args.tenant)
            totals = await backfill_runs(
                session, client, tenant_id=args.tenant, months=args.months,
            )
            after = await _snapshot(session, args.tenant)
            if args.dry_run:
                await session.rollback()
            else:
                await session.commit()
        report = {
            "dry_run": args.dry_run, "months": args.months, "tenant": args.tenant,
            "totals": totals, "before": before, "after": after,
        }
        print(json.dumps(report, indent=2, default=str))
        return 0
    finally:
        await engine.dispose()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="write nothing, print the counts")
    parser.add_argument("--months", type=int, default=24, help="pickup_at ≥ now − N months")
    parser.add_argument("--tenant", default=LJM_TENANT_ID)
    args = parser.parse_args()
    return asyncio.run(_run(args))


if __name__ == "__main__":
    raise SystemExit(main())
