"""The 32-state eastern-US footprint LJM serves.

Enforced server-side (a lead with `state='TX'` cannot enter the DB). Matches the frontend's
`IN_REGION_STATES` from the prior plan — canonical here now.
"""

IN_REGION_STATES: frozenset[str] = frozenset(
    {
        # Northeast
        "CT", "ME", "MA", "NH", "NJ", "NY", "PA", "RI", "VT",
        # Mid-Atlantic
        "DE", "DC", "MD", "VA", "WV",
        # Southeast
        "AL", "AR", "FL", "GA", "KY", "LA", "MS", "NC", "SC", "TN",
        # Midwest (eastern portion)
        "IL", "IN", "MI", "OH", "WI",
        # Extra reach the demo has always included
        "MO", "IA", "MN", "OK",
    }
)


def in_region(state: str | None) -> bool:
    return bool(state) and state.upper() in IN_REGION_STATES
