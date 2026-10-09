"""Vetting HTTP seam — single GET route, owner-only."""
from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Path, Request

from app.vetting.domain import normalize_key
from app.vetting.schemas import (
    AuthorityOut,
    PriorOut,
    RedFlagOut,
    VetReportOut,
)
from app.vetting.service import (
    FmcsaUnreachableError,
    LeadNotFoundError,
    vet_broker,
)

router = APIRouter(prefix="/vetting", tags=["vetting"])


@router.get("/{key}", response_model=VetReportOut)
async def get_vet(
    request: Request,
    key: str = Path(..., min_length=1, max_length=32),
) -> VetReportOut:
    if normalize_key(key) is None:
        raise HTTPException(422, "key must be numeric MC or DOT (optional MC-/DOT- prefix)")
    try:
        report = await vet_broker(request.app.state.sessionmaker, key)
    except LeadNotFoundError as exc:
        raise HTTPException(404, "unknown MC or DOT") from exc
    except FmcsaUnreachableError as exc:
        # Upstream FMCSA is unreachable AND we have no cache/lead to fall
        # back on — a transient dependency outage, not a 404. 503 lets the
        # client distinguish 'retry in a moment' from 'this MC/DOT doesn't
        # exist'. 15.28s hangs with a 404 verdict were the audit smell.
        raise HTTPException(
            503,
            "FMCSA snapshot unreachable and no cached data on file — try again shortly.",
            headers={"Retry-After": "30"},
        ) from exc

    today = datetime.now(UTC).date()
    age_days = (
        (today - report.snapshot.add_date).days if report.snapshot.add_date else None
    )
    return VetReportOut(
        key=report.key,
        mc=report.mc,
        dot=report.dot,
        legal_name=report.snapshot.legal_name,
        dba_name=report.snapshot.dba_name,
        verdict=report.verdict.band.value,
        red_flags=[
            RedFlagOut(code=f.code.value, reason=f.reason) for f in report.verdict.red_flags
        ],
        authority=AuthorityOut(
            status=report.snapshot.authority_status,
            add_date=report.snapshot.add_date.isoformat() if report.snapshot.add_date else None,
            age_days=age_days,
            oos_date=report.snapshot.oos_date.isoformat() if report.snapshot.oos_date else None,
        ),
        prior=PriorOut(
            last_sent_at=report.prior.last_sent_at.isoformat() if report.prior.last_sent_at else None,
            booked_count=report.prior.booked_count,
            rejected_count=report.prior.rejected_count,
        ),
        suppressed=report.suppressed,
        evidence_url=report.evidence_url,
        stale=report.stale,
        lead_id=report.lead_id,
    )
