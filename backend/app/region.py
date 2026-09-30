"""The 32-state eastern-US footprint LJM serves.

Enforced server-side (a lead with `state='TX'` cannot enter the DB). Matches the frontend's
`IN_REGION_STATES` from the prior plan — canonical here now.
"""

IN_REGION_STATES: frozenset[str] = frozenset(
    {
        # Northeast
        "CT",
        "ME",
        "MA",
        "NH",
        "NJ",
        "NY",
        "PA",
        "RI",
        "VT",
        # Mid-Atlantic
        "DE",
        "DC",
        "MD",
        "VA",
        "WV",
        # Southeast
        "AL",
        "AR",
        "FL",
        "GA",
        "KY",
        "LA",
        "MS",
        "NC",
        "SC",
        "TN",
        # Midwest (eastern portion)
        "IL",
        "IN",
        "MI",
        "OH",
        "WI",
        # Extra reach the demo has always included
        "MO",
        "IA",
        "MN",
        "OK",
    }
)


def in_region(state: str | None) -> bool:
    return bool(state) and state.upper() in IN_REGION_STATES


# Approximate bounding boxes per in-region state, in the (south, west, north, east)
# order Overpass QL expects. Values are conservative rectangles pulled from the
# Census Bureau / USGS state extents — a few decimals of imprecision is fine
# because we only need to bound one Overpass query per state and the results are
# re-filtered by `state` on ingest. The point is to keep each request bounded
# so Overpass doesn't reject it, not to be a GIS-grade shape.
STATE_BBOXES: dict[str, tuple[float, float, float, float]] = {
    # Northeast
    "CT": (40.98, -73.73, 42.05, -71.79),
    "ME": (43.06, -71.08, 47.46, -66.95),
    "MA": (41.24, -73.51, 42.89, -69.93),
    "NH": (42.70, -72.56, 45.31, -70.61),
    "NJ": (38.93, -75.56, 41.36, -73.89),
    "NY": (40.50, -79.76, 45.02, -71.86),
    "PA": (39.72, -80.52, 42.27, -74.69),
    "RI": (41.15, -71.86, 42.02, -71.12),
    "VT": (42.73, -73.44, 45.02, -71.46),
    # Mid-Atlantic
    "DE": (38.45, -75.79, 39.84, -75.05),
    "DC": (38.79, -77.12, 38.99, -76.91),
    "MD": (37.89, -79.49, 39.72, -75.05),
    "VA": (36.54, -83.68, 39.47, -75.24),
    "WV": (37.20, -82.65, 40.64, -77.72),
    # Southeast
    "AL": (30.14, -88.47, 35.01, -84.89),
    "AR": (33.00, -94.62, 36.50, -89.64),
    "FL": (24.40, -87.63, 31.00, -80.03),
    "GA": (30.36, -85.61, 35.00, -80.84),
    "KY": (36.50, -89.57, 39.15, -81.96),
    "LA": (28.93, -94.04, 33.02, -88.82),
    "MS": (30.17, -91.66, 34.99, -88.10),
    "NC": (33.84, -84.32, 36.59, -75.46),
    "SC": (32.03, -83.35, 35.22, -78.54),
    "TN": (34.98, -90.31, 36.68, -81.65),
    # Midwest (eastern portion)
    "IL": (36.97, -91.51, 42.51, -87.02),
    "IN": (37.77, -88.10, 41.76, -84.78),
    "MI": (41.70, -90.42, 48.31, -82.41),
    "OH": (38.40, -84.82, 42.33, -80.52),
    "WI": (42.49, -92.89, 47.31, -86.25),
    # Extra reach
    "MO": (35.99, -95.77, 40.61, -89.10),
    "IA": (40.38, -96.64, 43.50, -90.14),
    "MN": (43.50, -97.24, 49.38, -89.49),
    "OK": (33.62, -103.00, 37.00, -94.43),
}


def bbox_for_state(state: str) -> tuple[float, float, float, float] | None:
    """Return (south, west, north, east) for an in-region state, or None."""
    return STATE_BBOXES.get((state or "").upper())
