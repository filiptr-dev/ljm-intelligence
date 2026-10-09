"""EIA adapter — weekly diesel $/gal by PADD district."""
from app.integrations.adapters.eia.client import (
    PADD_SERIES_IDS,
    EIAClient,
    EIAError,
)

__all__ = ["PADD_SERIES_IDS", "EIAClient", "EIAError"]
