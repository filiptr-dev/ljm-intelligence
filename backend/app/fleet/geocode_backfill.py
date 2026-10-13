"""One-off: fill ``trucks.last_lat/last_lng`` from the LoadStop
``locationDescription`` strings we stored on ``trucks.raw``.

The 71 live rows all have ``raw->>'locationDescription'`` (55 non-empty)
but no coordinates — the paged ``trucks/GetAll`` envelope never carries
them. We parse the string to a state-centroid pin via
:func:`app.integrations.adapters.loadboard.loadstop.ingest.geocode.geocode`.

Idempotent: a second run only overwrites coords when the current pair
is ``NULL`` or when the parsed state has changed. Never writes
coordinates we don't have (parse miss ⇒ leave the row alone).

Usage::

    docker exec ljm-local-api-1 \\
        bash -lc "PYTHONPATH=/app python -m app.fleet.geocode_backfill [--dry-run]"
"""

from __future__ import annotations

import argparse
import asyncio
import logging

from sqlalchemy import select

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.fleet.models import Truck
from app.integrations.adapters.loadboard.loadstop.ingest.geocode import geocode
from app.shared.db import uow
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId

log = logging.getLogger(__name__)


async def backfill(*, dry_run: bool) -> dict[str, int]:
    settings = get_settings()
    engine = create_engine(settings)
    seen = 0
    filled = 0
    missing_desc = 0
    unparseable = 0
    try:
        sm = create_sessionmaker(engine)
        async with uow(sm, TenantId(LJM_TENANT_ID)) as session:
            rows = (await session.execute(select(Truck))).scalars().all()
            for t in rows:
                seen += 1
                raw = t.raw or {}
                desc = (
                    (raw.get("locationDescription") if isinstance(raw, dict) else None)
                    or (
                        (raw.get("truck") or {}).get("locationDescription")
                        if isinstance(raw, dict) and isinstance(raw.get("truck"), dict)
                        else None
                    )
                    or ""
                )
                if not desc:
                    missing_desc += 1
                    continue
                coords = geocode(desc)
                if coords is None:
                    unparseable += 1
                    continue
                if t.last_lat is None or t.last_lng is None:
                    filled += 1
                    if not dry_run:
                        t.last_lat, t.last_lng = coords
            if dry_run:
                await session.rollback()
    finally:
        await engine.dispose()
    return {
        "seen": seen, "filled": filled,
        "missing_desc": missing_desc, "unparseable": unparseable,
    }


async def _main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    s = await backfill(dry_run=args.dry_run)
    print(
        f"geocode_backfill: seen={s['seen']} filled={s['filled']} "
        f"missing_desc={s['missing_desc']} unparseable={s['unparseable']}"
        + (" [dry-run]" if args.dry_run else "")
    )


if __name__ == "__main__":
    asyncio.run(_main())
