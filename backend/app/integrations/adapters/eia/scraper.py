"""EIA public weekly diesel scraper — keyless.

Public page shape (per PADD district):
``https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_<code>_w.htm``

The page is an HTML table. The header row has six ``<th class="Series5">MM/DD/YY</th>``
dates (two-digit year, 2000-based). For each metric row there are six ``<td>``
cells matching those dates; the sixth has class ``Current2`` and is the latest
weekly value. We only pick the ``Diesel (On-Highway) - All Types`` metric.

Keyless by design — no EIA API key required. The site needs a browser-style
``User-Agent`` header or it returns 403.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

import httpx

log = logging.getLogger(__name__)

EIA_PAGE_TMPL: Final[str] = "https://www.eia.gov/dnav/pet/pet_pri_gnd_dcus_{code}_w.htm"
_UA: Final[str] = "Mozilla/5.0 (compatible; ljm-freight-demo/1.0; +https://eia.gov)"

# PADD district → EIA page code. The sub-PADDs (1A/1B/1C) and the four other
# PADDs match the series keys the API client already uses (see client.py).
PADD_PAGE_CODES: Final[dict[str, str]] = {
    "1A": "r1x",
    "1B": "r1y",
    "1C": "r1z",
    "2":  "r20",
    "3":  "r30",
    "4":  "r40",
    "5":  "r50",
}

# "Diesel (On-Highway) - All Types" row — followed by six price cells, the
# last of which (class ``Current2``) is the newest week. We tolerate stray
# whitespace in the markup.
_DIESEL_ROW_RE: Final[re.Pattern[str]] = re.compile(
    r"Diesel\s*\(On-Highway\)\s*-\s*All\s*Types.*?"
    r"((?:<td[^>]*class=\"(?:DataB|Current2)\"[^>]*>[^<]*</td>\s*){6})",
    re.DOTALL | re.IGNORECASE,
)
_CELL_RE: Final[re.Pattern[str]] = re.compile(
    r"<td[^>]*class=\"(DataB|Current2)\"[^>]*>\s*([^<\s]+)\s*</td>",
    re.IGNORECASE,
)
_DATE_RE: Final[re.Pattern[str]] = re.compile(
    r'<th[^>]*class="Series5"[^>]*>\s*(\d{2}/\d{2}/\d{2})\s*</th>',
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DieselRow:
    padd: str
    week_of: date
    price_usd: Decimal


class EIAScrapeError(RuntimeError):
    pass


def _parse_latest(html: str) -> tuple[date, Decimal] | None:
    """Return (week_of, price) from one PADD page, or ``None`` if the row
    can't be found or the latest cell isn't parseable."""
    dates = _DATE_RE.findall(html)
    if not dates:
        return None
    m = _DIESEL_ROW_RE.search(html)
    if not m:
        return None
    cells = _CELL_RE.findall(m.group(1))
    if not cells:
        return None
    # The newest-week cell is marked class="Current2"; it's the last of the
    # six, and the corresponding header date is also the last.
    latest_cell = next(((cls, val) for cls, val in reversed(cells) if cls.lower() == "current2"), None)
    if latest_cell is None:
        return None
    _, raw_price = latest_cell
    try:
        price = Decimal(raw_price)
    except (InvalidOperation, ValueError):
        return None
    try:
        # EIA writes the two-digit year with a 2000 base ("26" → 2026).
        week_of = datetime.strptime(dates[-1], "%m/%d/%y").date()  # noqa: DTZ007 — date-only; .date() strips tz immediately
    except ValueError:
        return None
    return week_of, price


async def fetch_latest_by_padd(
    http: httpx.AsyncClient | None = None, *, timeout_s: float = 20.0
) -> list[DieselRow]:
    """Fetch each PADD page and return the latest weekly diesel row per
    district. A per-PADD failure is logged and skipped — the caller gets
    whatever we could parse."""
    out: list[DieselRow] = []
    owns_client = http is None
    client = http or httpx.AsyncClient(timeout=timeout_s, headers={"User-Agent": _UA})
    try:
        for padd, code in PADD_PAGE_CODES.items():
            url = EIA_PAGE_TMPL.format(code=code)
            try:
                r = await client.get(url, headers={"User-Agent": _UA}, timeout=timeout_s)
                if r.status_code >= 400:
                    log.warning("eia.scrape: %s -> HTTP %s", url, r.status_code)
                    continue
                parsed = _parse_latest(r.text)
            except (httpx.HTTPError, EIAScrapeError) as exc:
                log.warning("eia.scrape: %s failed: %s", url, exc)
                continue
            if not parsed:
                log.warning("eia.scrape: %s unparseable — diesel row not found", url)
                continue
            week_of, price = parsed
            out.append(DieselRow(padd=padd, week_of=week_of, price_usd=price))
    finally:
        if owns_client:
            await client.aclose()
    return out
