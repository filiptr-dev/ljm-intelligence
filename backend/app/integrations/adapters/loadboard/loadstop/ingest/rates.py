"""Lane rates ingest — LoadStop ``globalLaneRates`` → ``lane_rates``."""

from __future__ import annotations

from typing import Any, Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.rates.models import LaneRate
from app.shared.orm import LJM_TENANT_ID

SOURCE = "loadstop"


class _ClientLike(Protocol):
    async def paged(self, service, method, *, page_size=200, extra=None): ...


def _project(row: dict[str, Any]) -> dict[str, Any] | None:
    sid = str(row.get("id") or "")
    if not sid:
        return None
    return {
        "source_ref": sid,
        "origin_state": ((row.get("originState") or "")[:2]).upper() or None,
        "origin_city": row.get("originCity"),
        "dest_state": ((row.get("destState") or "")[:2]).upper() or None,
        "dest_city": row.get("destCity"),
        "equipment": row.get("equipment"),
        "as_of_month": row.get("asOfMonth") or row.get("month"),
        "low": row.get("low"),
        "avg": row.get("avg") or row.get("average"),
        "high": row.get("high"),
        "miles": row.get("miles"),
        "raw": row,
    }


async def ingest_rates(
    session: AsyncSession,
    client: _ClientLike,
    *,
    tenant_id: str = LJM_TENANT_ID,
) -> dict[str, int]:
    rows_in = 0
    upserted = 0
    async for page in client.paged("globalLaneRates", "GetAll", page_size=200):
        for raw in page:
            rows_in += 1
            p = _project(raw)
            if p is None:
                continue
            existing = (
                await session.execute(
                    select(LaneRate).where(
                        LaneRate.tenant_id == tenant_id,
                        LaneRate.source == SOURCE,
                        LaneRate.source_ref == p["source_ref"],
                    )
                )
            ).scalar_one_or_none()
            if existing is None:
                session.add(
                    LaneRate(
                        tenant_id=tenant_id,
                        source=SOURCE,
                        **{k: v for k, v in p.items()},
                    )
                )
            else:
                for k in ("low", "avg", "high", "miles", "raw", "as_of_month"):
                    setattr(existing, k, p.get(k) if p.get(k) is not None else getattr(existing, k))
            upserted += 1
    await session.flush()
    return {"rows_in": rows_in, "rows_upserted": upserted}
