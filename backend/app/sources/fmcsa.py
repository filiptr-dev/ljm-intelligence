"""FMCSA Company Census SODA source.

Endpoint: https://data.transportation.gov/resource/az4n-8mr2.json (no key required).
Filters:
  - status_code = 'A'              (active carriers/brokers/shippers)
  - carship contains 'B'|'S'|'F'   (broker / shipper / forwarder)
  - phy_state IN IN_REGION_STATES
  - order by add_date desc

`X-App-Token` is optional and only raises rate limits — we don't require it.

Why not scrape SAFER HTML: this endpoint is the same data as public, licence-clean,
and keyless. It's the one authoritative source we can lean on without ToS risk.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.region import IN_REGION_STATES
from app.sources.emails import normalize_email

log = logging.getLogger(__name__)

SODA_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"


@dataclass(frozen=True, slots=True)
class DiscoveredLead:
    id: str  # "MC-…" or "DOT-…"
    mc: str | None
    dot: str | None
    domain: str | None
    name: str
    kind: str  # Broker / Shipper / Forwarder
    state: str
    city: str | None
    address: str | None
    phone: str | None
    primary_email: str | None
    raw: dict


def _classify(carship: str) -> str | None:
    """Map SODA `carship` (letters like 'B', 'S;C', 'F') to a lead kind.

    Priority Broker > Shipper > Forwarder. Pure carrier ('C' only) → None (excluded — competitor).
    """
    letters = {c.strip().upper() for c in carship.replace(";", ",").split(",") if c.strip()}
    if letters == {"C"}:
        return None
    if "B" in letters:
        return "Broker"
    if "S" in letters:
        return "Shipper"
    if "F" in letters:
        return "Forwarder"
    return None


def _row_to_lead(row: dict) -> DiscoveredLead | None:
    state = (row.get("phy_state") or "").upper().strip()
    if state not in IN_REGION_STATES:
        return None
    kind = _classify(row.get("carship") or "")
    if not kind:
        return None
    # SODA az4n-8mr2 schema: `docket1prefix` + `docket1` form the MC/MX/FF number.
    prefix = (row.get("docket1prefix") or "").strip().upper()
    docket = (row.get("docket1") or "").strip()
    mc = docket if (docket and prefix == "MC") else None
    dot = (row.get("dot_number") or "").strip() or None
    if not (mc or dot):
        return None
    lead_id = f"MC-{mc}" if mc else f"DOT-{dot}"
    return DiscoveredLead(
        id=lead_id,
        mc=mc,
        dot=dot,
        domain=None,  # SODA doesn't ship a domain; enriched later by Gemini/fetcher
        name=(row.get("legal_name") or "").strip() or "(unknown)",
        kind=kind,
        state=state,
        city=(row.get("phy_city") or "").strip() or None,
        address=(row.get("phy_street") or "").strip() or None,
        phone=(row.get("phone") or "").strip() or None,
        # az4n-8mr2 ships `email_address` (often upper-case); normalised, junk dropped.
        primary_email=normalize_email(row.get("email_address")),
        raw=row,
    )


async def fetch_fmcsa(*, limit: int = 500, app_token: str | None = None) -> list[DiscoveredLead]:
    """One page of the freshest FMCSA rows in-region, mapped to `DiscoveredLead`.

    `limit` bounds the SODA `$limit`. The SODA `$where` filters carship + status in-server so
    we don't pull tens of thousands of carrier rows and drop them client-side.
    """
    where = "status_code='A' AND (carship LIKE '%B%' OR carship LIKE '%S%' OR carship LIKE '%F%')"
    params = {
        "$select": ",".join(
            [
                "dot_number",
                "docket1prefix",
                "docket1",
                "legal_name",
                "carship",
                "phy_state",
                "phy_city",
                "phy_street",
                "phone",
                "email_address",
                "status_code",
                "add_date",
            ]
        ),
        "$where": where,
        "$order": "add_date DESC",
        "$limit": str(limit),
    }
    headers = {"User-Agent": "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"}
    if app_token:
        headers["X-App-Token"] = app_token
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.get(SODA_URL, params=params, headers=headers)
        resp.raise_for_status()
        rows = resp.json()
    leads: list[DiscoveredLead] = []
    seen: set[str] = set()
    for row in rows:
        lead = _row_to_lead(row)
        if lead is None:
            continue
        if lead.id in seen:
            continue
        seen.add(lead.id)
        leads.append(lead)
    log.info("fmcsa: %d rows fetched, %d in-region leads", len(rows), len(leads))
    return leads
