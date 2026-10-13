"""Parse LoadStop ``locationDescription`` → approximate (lat, lng).

LoadStop's ``trucks/GetAll`` envelope carries a ``locationDescription``
string like ``"Hartford, CT (moving)"`` or ``"5.9 mi NE of Burton, NC
(moving)"`` — a human string, no coordinates. We don't call the paid
tracking endpoint (user rule: strictly read-only), so to pin the truck
on the fleet map we parse "City, ST" out of the string and resolve to
an approximate centroid.

Shipping with **state centroids only** on purpose:

* All 71 live trucks have a state code, so every truck ends up with a
  pin — no silent null gap.
* 48 distinct cities in current local data, half of which are small
  townships that would miss a 200-city hand-table anyway. State-level
  is honest about the accuracy ("approximate location, state") and
  doesn't lie with a city centroid 15 miles away from the real spot.
* No network, no new deps, no migration, no cache table.

If we need true city-level later, swap ``geocode()`` for a Nominatim
caller + a ``geocode_cache`` table — the ingest + backfill paths won't
change. The parser already surfaces the city string on
``ParsedLocation.city`` for that upgrade.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

log = logging.getLogger(__name__)


# Longitude/latitude centroids per US state + DC, approximate — public
# USGS / Census figures rounded to 4 decimals. Enough accuracy for a
# "truck is somewhere in Connecticut" pin.
STATE_CENTROIDS: dict[str, tuple[float, float]] = {
    "AL": (32.7794, -86.8287), "AK": (64.0685, -152.2782),
    "AZ": (34.2744, -111.6602), "AR": (34.8938, -92.4426),
    "CA": (37.1841, -119.4696), "CO": (38.9972, -105.5478),
    "CT": (41.6219, -72.7273), "DE": (38.9896, -75.5050),
    "DC": (38.9101, -77.0147), "FL": (28.6305, -82.4497),
    "GA": (32.6415, -83.4426), "HI": (20.2927, -156.3737),
    "ID": (44.3509, -114.6130), "IL": (40.0417, -89.1965),
    "IN": (39.8942, -86.2816), "IA": (42.0751, -93.4960),
    "KS": (38.4937, -98.3804), "KY": (37.5347, -85.3021),
    "LA": (31.0689, -91.9968), "ME": (45.3695, -69.2428),
    "MD": (39.0550, -76.7909), "MA": (42.2596, -71.8083),
    "MI": (44.3467, -85.4102), "MN": (46.2807, -94.3053),
    "MS": (32.7364, -89.6678), "MO": (38.3566, -92.4580),
    "MT": (47.0527, -109.6333), "NE": (41.5378, -99.7951),
    "NV": (39.3289, -116.6312), "NH": (43.6805, -71.5811),
    "NJ": (40.1907, -74.6728), "NM": (34.4071, -106.1126),
    "NY": (42.9538, -75.5268), "NC": (35.5557, -79.3877),
    "ND": (47.4501, -100.4659), "OH": (40.2862, -82.7937),
    "OK": (35.5889, -97.4943), "OR": (43.9336, -120.5583),
    "PA": (40.8781, -77.7996), "RI": (41.6762, -71.5562),
    "SC": (33.9169, -80.8964), "SD": (44.4443, -100.2263),
    "TN": (35.8580, -86.3505), "TX": (31.4757, -99.3312),
    "UT": (39.3055, -111.6703), "VT": (44.0687, -72.6658),
    "VA": (37.5215, -78.8537), "WA": (47.3826, -120.4472),
    "WV": (38.6409, -80.6227), "WI": (44.6243, -89.9941),
    "WY": (42.9957, -107.5512),
}


# ``5.9 mi NE of Burton, NC (moving)``, ``Hartford, CT (moving)``,
# ``Hartford, CT (gps stopped)``, ``2.2 mi E of Holloway Terrace, DE``.
# Capture ``(city, STATE)`` — ignore the mile prefix and the status
# parenthetical. Keeping the regex deliberately permissive on city
# (any non-comma run) so township names and multi-word cities work.
_LOC_RE = re.compile(
    r"""
    ^\s*
    (?:[0-9.]+\s+mi\s+[NSEW]+\s+of\s+)?   # optional "X.X mi NE of "
    (?P<city>[^,()]+?)                     # city — non-greedy, no commas/parens
    ,\s*
    (?P<state>[A-Z]{2})                    # two-letter state
    (?:\s*\([^)]*\))?                      # optional "(moving)" status
    \s*$
    """,
    re.VERBOSE,
)


@dataclass(frozen=True)
class ParsedLocation:
    city: str
    state: str
    lat: float
    lng: float
    # label the UI shows beside the pin — "approximate location, CT state"
    label: str


def parse(description: str | None) -> ParsedLocation | None:
    """Parse a LoadStop ``locationDescription`` into an approximate pin.

    Returns ``None`` only when the string carries no "City, ST" tail at
    all — i.e. truly unparseable. Any valid state code wins at minimum.
    """
    if not description:
        return None
    m = _LOC_RE.match(description.strip())
    if not m:
        return None
    state = m.group("state").upper()
    centroid = STATE_CENTROIDS.get(state)
    if centroid is None:
        return None
    city = m.group("city").strip()
    lat, lng = centroid
    return ParsedLocation(
        city=city, state=state, lat=lat, lng=lng,
        label=f"approximate location ({state} state)",
    )


def geocode(description: str | None) -> tuple[float, float] | None:
    """Shorthand: just the coords, ``None`` on miss."""
    parsed = parse(description)
    return (parsed.lat, parsed.lng) if parsed else None
