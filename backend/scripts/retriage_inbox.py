"""Re-run :func:`app.inbox.triage.triage_message` over every stored mail row
for the LJM tenant, after deleting the stale ``message_insights`` +
``no_reply_tracker`` rows the previous classifier produced.

Run inside the api container::

    docker exec ljm-local-api-1 \
        bash -lc "PYTHONPATH=/app python /app/scripts/retriage_inbox.py"

Idempotent; one UoW; commit on exit.
"""
from __future__ import annotations

import asyncio

from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.inbox.triage import retriage_all
from app.shared.db import uow
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId


async def _main() -> None:
    settings = get_settings()
    engine = create_engine(settings)
    try:
        sm = create_sessionmaker(engine)
        async with uow(sm, TenantId(LJM_TENANT_ID)) as session:
            n = await retriage_all(session)
            print(f"retriaged {n} messages")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(_main())
