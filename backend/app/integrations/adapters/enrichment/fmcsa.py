"""FMCSA Company Census SODA source.

Endpoint: https://data.transportation.gov/resource/az4n-8mr2.json (no key required).
Filters (all pushed into SoQL ``$where`` so the wire pull is already useful):
  - ``status_code = 'A'``               (active carriers/brokers/shippers)
  - ``carship LIKE '%B%'/'%S%'/'%F%'``  (broker / shipper / forwarder)
  - ``phy_state IN (...)``              (the 33-state in-region set; see ``region.py``)

Ordering + pagination:
  - ``$order add_date DESC, dot_number DESC`` — newest first, stable tie-break.
  - **Keyset**, not ``$offset``. SODA's ``$offset`` is O(offset) server-side and
    slows down per page; keyset on the sort columns stays O(page_size). SoQL has
    no row-value comparison, so page 2+ predicates it as
    ``add_date < :d OR (add_date = :d AND dot_number < :n)``, where ``:d``/``:n``
    come from the last row of the previous page.
  - Column types (verified against the SODA dataset): ``dot_number`` is a
    **numeric** SoQL column — compared as a bare int literal (no quotes).
    ``add_date`` is 8-character ``YYYYMMDD`` **text**, so lexical string order
    matches chronological order — safe to compare with ``<`` / ``=`` against a
    quoted literal.

``X-App-Token`` stays optional — keyless we're on the SODA shared throttle bucket
(fine at slice-1 volumes); a token only raises the ceiling.

Why not scrape SAFER HTML: this endpoint is the same data as public, licence-clean,
and keyless. It's the one authoritative source we can lean on without ToS risk.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

from app.integrations.adapters.web.emails import normalize_email
from app.shared.region import IN_REGION_STATES

log = logging.getLogger(__name__)

SODA_URL = "https://data.transportation.gov/resource/az4n-8mr2.json"

# SoQL ``$select`` — kept as a single string literal (ruff FLY002) but
# split per-column so a git diff on a schema tweak stays one line.
_SELECT_COLS = (
    "dot_number,"
    "docket1prefix,"
    "docket1,"
    "legal_name,"
    "carship,"
    "phy_state,"
    "phy_city,"
    "phy_street,"
    "phone,"
    "email_address,"
    "status_code,"
    "add_date"
)


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
    # Safety net: the ``$where`` clause already pins ``phy_state`` to the
    # in-region set, so this branch should never trip in practice. Kept
    # (cheap) as defence against a malformed row / future ``$select`` drift.
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


def _build_where(cursor: tuple[str, str] | None) -> str:
    """SoQL ``$where`` — region + kind + optional keyset predicate.

    ``phy_state IN ('AL','AR',…)`` is built from ``region.IN_REGION_STATES`` so
    the source of truth stays in one place. Sorted for a stable, cacheable URL.
    """
    states = ",".join(f"'{s}'" for s in sorted(IN_REGION_STATES))
    parts = [
        "status_code='A'",
        f"phy_state IN ({states})",
        "(carship LIKE '%B%' OR carship LIKE '%S%' OR carship LIKE '%F%')",
    ]
    if cursor is not None:
        add_date, dot_number = cursor
        # SoQL has no (a,b) < (x,y) row-value form — spelled out longhand.
        # ``add_date`` is 8-char YYYYMMDD text (lexical == chronological);
        # ``dot_number`` is numeric, so the int literal is unquoted.
        parts.append(f"(add_date < '{add_date}' OR (add_date = '{add_date}' AND dot_number < {dot_number}))")
    return " AND ".join(parts)


def _cursor_from_page(rows: list[dict]) -> tuple[str, str] | None:
    """Extract (add_date, dot_number) from the last row of a page, for the next keyset."""
    if not rows:
        return None
    last = rows[-1]
    add_date = last.get("add_date")
    dot_number = last.get("dot_number")
    if not add_date or dot_number in (None, ""):
        return None
    # dot_number is numeric in SoQL — cast to a bare int literal for the next $where.
    try:
        dot_int = int(str(dot_number).strip())
    except (TypeError, ValueError):
        return None
    return (str(add_date), str(dot_int))


async def fetch_fmcsa(
    *,
    page_size: int = 500,
    max_pages: int = 1,
    app_token: str | None = None,
    client: httpx.AsyncClient | None = None,
) -> AsyncIterator[list[DiscoveredLead]]:
    """Async generator over pages of freshest in-region FMCSA rows.

    Yields one page (``list[DiscoveredLead]``) per SODA request. Stops when either
    the last page returned fewer than ``page_size`` rows (exhausted) or
    ``max_pages`` have been yielded (caller-side cap).

    ``client`` is injectable for tests — pass an ``httpx.AsyncClient`` wired to a
    ``MockTransport`` (see ``tests/test_fmcsa_source.py``) to avoid the network.
    """
    headers = {"User-Agent": "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"}
    if app_token:
        headers["X-App-Token"] = app_token

    owned = client is None
    if owned:
        client = httpx.AsyncClient(timeout=20.0)

    try:
        cursor: tuple[str, str] | None = None
        pages_yielded = 0
        while pages_yielded < max_pages:
            params = {
                "$select": _SELECT_COLS,
                "$where": _build_where(cursor),
                "$order": "add_date DESC, dot_number DESC",
                "$limit": str(page_size),
            }
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
            log.info(
                "fmcsa: page %d — %d rows fetched, %d in-region leads",
                pages_yielded + 1,
                len(rows),
                len(leads),
            )
            yield leads
            pages_yielded += 1

            # Exhaustion: a short page means SODA has no more matching rows.
            if len(rows) < page_size:
                return
            # Advance keyset from the last raw row (not the filtered leads — a page
            # of all-carriers is still forward progress on ``add_date``).
            next_cursor = _cursor_from_page(rows)
            if next_cursor is None:
                # Missing sort keys → can't safely page forward; stop rather than loop.
                return
            cursor = next_cursor
    finally:
        if owned:
            await client.aclose()
