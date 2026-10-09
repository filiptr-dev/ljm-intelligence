"""Repository seam — SQL + FMCSA calls, zero business logic.

Three reads, zero writes. ``service.py`` only talks to this file (and the
pure domain). Keeps the onion rule: outer rings depend inward.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.outreach.models import CallOutcome, SentLog, Suppression
from app.prospecting.models import FmcsaSnapshotCache, Lead
from app.vetting.domain import BrokerSnapshot, PriorContact

log = logging.getLogger(__name__)

SNAPSHOT_TTL = timedelta(days=30)


@dataclass(frozen=True, slots=True)
class SnapshotRow:
    snapshot: BrokerSnapshot
    stale: bool
    evidence_url: str
    cache_hit: bool


def _parse_date(raw: str | None) -> date | None:
    """FMCSA add_date is YYYYMMDD text; snapshot payload uses ISO or epoch-ish strings."""
    if not raw:
        return None
    raw = str(raw).strip()
    if len(raw) == 8 and raw.isdigit():
        try:
            return date(int(raw[:4]), int(raw[4:6]), int(raw[6:8]))
        except ValueError:
            return None
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=UTC).date()
        except ValueError:
            continue
    return None


def extract_snapshot_fields(payload: dict, *, mc: str | None, dot: str | None) -> BrokerSnapshot:
    """Pull the shape ``domain.vet()`` needs out of the raw FMCSA snapshot.

    Shape varies — defensive ``.get()`` chains, never fail hard.
    """
    carrier: dict = {}
    if isinstance(payload, dict):
        content = payload.get("content") or {}
        if isinstance(content, dict):
            carrier = content.get("carrier") or {}
            if not isinstance(carrier, dict):
                carrier = {}
    legal = carrier.get("legalName") or carrier.get("legal_name")
    dba = carrier.get("dbaName") or carrier.get("dba_name")
    status = carrier.get("statusCode") or carrier.get("status_code") or carrier.get("allowedToOperate")
    # ``allowedToOperate`` is "Y"/"N"; map to a status code domain understands.
    if status in ("N", "NO"):
        status = "R"
    elif status in ("Y", "YES"):
        status = "A"
    phone = carrier.get("telephone") or carrier.get("phone")
    email = carrier.get("emailAddress") or carrier.get("email")
    oos = carrier.get("oosDate") or carrier.get("oos_date")
    add = carrier.get("addDate") or carrier.get("add_date") or carrier.get("mcs150Date")
    return BrokerSnapshot(
        mc=mc,
        dot=str(dot) if dot else None,
        legal_name=legal,
        dba_name=dba,
        authority_status=status,
        add_date=_parse_date(add),
        oos_date=_parse_date(oos),
        phone=str(phone) if phone else None,
        email=email,
    )


async def read_fmcsa_cache(s: AsyncSession, dot: str) -> tuple[dict | None, datetime | None]:
    row = (
        await s.execute(select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == str(dot)))
    ).scalar_one_or_none()
    if row is None:
        return None, None
    return row.payload, row.fetched_at


async def write_fmcsa_cache(s: AsyncSession, dot: str, payload: dict) -> None:
    """Upsert — SQLite-friendly path for tests, PG upsert on Neon."""
    existing = (
        await s.execute(select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == str(dot)))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        s.add(FmcsaSnapshotCache(dot=str(dot), payload=payload, fetched_at=now))
    else:
        existing.payload = payload
        existing.fetched_at = now
    await s.commit()


async def live_fmcsa_fetch(dot: str) -> dict | None:
    """One snapshot GET. Returns the raw payload or None on any failure.

    Reuses the exact URL template from the existing FMCSA adapter so there
    is one place that calls the public endpoint.
    """
    import httpx

    from app.integrations.adapters.enrichment.fmcsa import SNAPSHOT_URL_TMPL

    headers = {"User-Agent": "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"}
    url = SNAPSHOT_URL_TMPL.format(dot=dot)
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, headers=headers)
            if resp.status_code != 200:
                return None
            return resp.json()
    except Exception as exc:  # noqa: BLE001
        log.info("vetting: live FMCSA fetch failed for dot=%s: %s", dot, exc)
        return None


async def prior_contact(s: AsyncSession, lead_id: str) -> PriorContact:
    """One lead → last send + win/reject tally. Zero rows is fine."""
    last_sent = (
        await s.execute(
            select(func.max(SentLog.sent_at)).where(SentLog.lead_id == lead_id)
        )
    ).scalar_one_or_none()
    booked = int(
        (
            await s.execute(
                select(func.count())
                .select_from(CallOutcome)
                .where(CallOutcome.lead_id == lead_id, CallOutcome.outcome == "booked")
            )
        ).scalar_one()
        or 0
    )
    rejected = int(
        (
            await s.execute(
                select(func.count())
                .select_from(CallOutcome)
                .where(
                    CallOutcome.lead_id == lead_id,
                    CallOutcome.outcome == "not_interested",
                )
            )
        ).scalar_one()
        or 0
    )
    return PriorContact(last_sent_at=last_sent, booked_count=booked, rejected_count=rejected)


async def find_lead_by_mc_or_dot(s: AsyncSession, mc: str | None, dot: str | None) -> Lead | None:
    if mc:
        row = (
            await s.execute(select(Lead).where(Lead.mc == mc).limit(1))
        ).scalar_one_or_none()
        if row:
            return row
    if dot:
        row = (
            await s.execute(select(Lead).where(Lead.dot == str(dot)).limit(1))
        ).scalar_one_or_none()
        if row:
            return row
    return None


async def is_suppressed(s: AsyncSession, email: str | None) -> bool:
    if not email:
        return False
    row = (
        await s.execute(select(Suppression.email).where(Suppression.email == email.lower()))
    ).scalar_one_or_none()
    return row is not None
