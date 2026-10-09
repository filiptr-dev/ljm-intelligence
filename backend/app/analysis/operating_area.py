"""The carrier's operating area: the one list the demo seed, the lanes API and the map share.

The client runs the states east of Texas's eastern border, up to Canada. Texas itself
and everything west of (or straight north of) it is outside the area. Louisiana and
Arkansas are the first states in; DC is included because it is on the I-95 corridor.
"""

from __future__ import annotations

OPERATING_STATES: tuple[str, ...] = (
    "LA", "AR", "MO", "IA", "MN", "WI", "IL", "MI", "IN", "OH", "KY", "TN", "MS", "AL", "GA", "FL",
    "SC", "NC", "VA", "WV", "MD", "DE", "PA", "NJ", "NY", "CT", "RI", "MA", "VT", "NH", "ME", "DC",
)

OPERATING_STATE_SET: frozenset[str] = frozenset(OPERATING_STATES)

def in_operating_area(state: str | None) -> bool:
    return (state or "").strip().upper() in OPERATING_STATE_SET
