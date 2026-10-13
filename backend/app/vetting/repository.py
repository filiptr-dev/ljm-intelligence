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


def normalize_dot(dot: str | int | None) -> str | None:
    """Strip leading zeros so '01031258' and '1031258' hit the same cache row.

    LoadStop stores DOTs as zero-padded strings (legacy ABP shape); FMCSA
    SODA returns them unpadded. Without normalisation the cache keyed by
    the padded form never hits on a lookup that came in through SODA, so
    every refresh re-queries SODA. One canonical digit-only form is enough.
    """
    if dot is None:
        return None
    raw = str(dot).strip()
    if not raw:
        return None
    stripped = raw.lstrip("0")
    return stripped or "0"


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
    key = normalize_dot(dot) or str(dot)
    row = (
        await s.execute(select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == key))
    ).scalar_one_or_none()
    if row is None:
        return None, None
    return row.payload, row.fetched_at


async def write_fmcsa_cache(s: AsyncSession, dot: str, payload: dict) -> None:
    """Upsert — SQLite-friendly path for tests, PG upsert on Neon."""
    key = normalize_dot(dot) or str(dot)
    existing = (
        await s.execute(select(FmcsaSnapshotCache).where(FmcsaSnapshotCache.dot == key))
    ).scalar_one_or_none()
    now = datetime.now(UTC)
    if existing is None:
        s.add(FmcsaSnapshotCache(dot=key, payload=payload, fetched_at=now))
    else:
        existing.payload = payload
        existing.fetched_at = now
    await s.commit()


def _soda_row_to_snapshot_payload(row: dict) -> dict:
    """Wrap a SODA ``az4n-8mr2`` row in the ``content.carrier`` shape that
    ``extract_snapshot_fields`` already understands.

    Kept here (next to the live fetch) so there is one translation point
    between the SODA schema and the vetting domain shape. SODA ships
    ``snake_case`` field names; we re-key only what the domain cares about
    and leave the raw row under ``_soda_raw`` for future audit.
    """
    def _nz(v):
        return v if (v is not None and str(v).strip() != "") else None

    carrier = {
        "legalName": _nz(row.get("legal_name")),
        "dbaName": _nz(row.get("dba_name")),
        # SODA ships status_code as "A" (active) / "I" (inactive).
        # The az4n-8mr2 Company Census does NOT carry a revoked bit; a
        # separate authority dataset would be needed. We never fabricate
        # "A" — if the field is missing/blank, extract_snapshot_fields
        # yields authority_status=None which the rule table flags as
        # authority_unverified (caution), by design.
        "statusCode": _nz(row.get("status_code")),
        "addDate": _nz(row.get("add_date")),
        # Census file has no out-of-service date column; leave unknown
        # rather than invent one.
        "oosDate": None,
        "telephone": _nz(row.get("phone")),
        "emailAddress": _nz(row.get("email_address")),
        "dotNumber": _nz(row.get("dot_number")),
        "docket1prefix": _nz(row.get("docket1prefix")),
        "docket1": _nz(row.get("docket1")),
    }
    return {"content": {"carrier": carrier}, "_soda": {"dataset": "az4n-8mr2", "row": row}}


async def _soda_get(params: dict) -> list[dict]:
    """One keyless SODA GET against ``data.transportation.gov`` az4n-8mr2.

    Bounded 5s read, 2s connect — a user is watching a spinner. Optional
    ``FMCSA_APP_TOKEN`` only raises the throttle ceiling; never required.
    """
    import os

    import httpx

    url = "https://data.transportation.gov/resource/az4n-8mr2.json"
    headers = {"User-Agent": "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"}
    token = os.environ.get("FMCSA_APP_TOKEN") or ""
    if token:
        headers["X-App-Token"] = token
    timeout = httpx.Timeout(connect=2.0, read=5.0, write=5.0, pool=5.0)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers=headers)
            if resp.status_code != 200:
                return []
            data = resp.json()
            return data if isinstance(data, list) else []
    except Exception as exc:  # noqa: BLE001
        log.info("vetting: SODA fetch failed params=%s: %s", params, exc)
        return []


async def live_fmcsa_fetch(dot: str) -> dict | None:
    """Fetch the FMCSA Company Census row for a DOT via SODA (keyless).

    Replaces the 2026-10 SAFER snapshot path (``mobile.fmcsa.dot.gov/qc``)
    which now 403s. SODA ``az4n-8mr2`` is the public, licence-clean,
    keyless FMCSA census — same source the prospecting pipeline already
    uses (see ``integrations/adapters/enrichment/fmcsa.py``).

    Returns a payload shaped as ``{"content": {"carrier": {...}}}`` so
    ``extract_snapshot_fields`` keeps working unchanged. Never fabricates
    an authority_status; missing fields stay ``None``.
    """
    if not dot or not str(dot).strip().isdigit():
        return None
    rows = await _soda_get(
        {
            "$select": "dot_number,legal_name,dba_name,status_code,add_date,phone,email_address,docket1prefix,docket1",
            "$where": f"dot_number={int(str(dot).strip())}",
            "$limit": "1",
        }
    )
    if not rows:
        return None
    return _soda_row_to_snapshot_payload(rows[0])


async def live_fmcsa_fetch_by_mc(mc: str) -> dict | None:
    """SODA lookup by MC docket (``docket1prefix='MC' AND docket1=<mc>``).

    Used when the caller has an MC but no DOT in local leads. On success
    the returned payload embeds ``dotNumber`` so the service can key the
    cache row by DOT (unchanged cache schema).
    """
    if not mc or not str(mc).isdigit():
        return None
    rows = await _soda_get(
        {
            "$select": "dot_number,legal_name,dba_name,status_code,add_date,phone,email_address,docket1prefix,docket1",
            "$where": f"docket1prefix='MC' AND docket1='{str(mc).strip()}'",
            "$limit": "1",
        }
    )
    if not rows:
        return None
    return _soda_row_to_snapshot_payload(rows[0])


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
        # Leads store the LoadStop zero-padded form ("01031258"); callers
        # often arrive with the SODA unpadded form ("1031258"). Match both.
        norm = normalize_dot(dot) or str(dot)
        row = (
            await s.execute(
                select(Lead)
                .where(Lead.dot.in_([str(dot), norm]))
                .limit(1)
            )
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
