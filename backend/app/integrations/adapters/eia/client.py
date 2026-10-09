"""EIA v2 Open Data — weekly on-highway diesel by PADD district.

Series key shape (EIA v2): ``PET.EMD_EPD2D_PTE_R{code}_DPG``.

- `R10` → PADD 1 (East Coast) — aggregated
- `R1X` → PADD 1A / 1B / 1C  (we fetch the sub-districts, not R10)
- `R20` → PADD 2  (Midwest)
- `R30` → PADD 3  (Gulf)
- `R40` → PADD 4  (Rocky Mountain)
- `R50` → PADD 5  (West Coast)

The adapter is a thin HTTP wrapper. The service decides what to do when the
key is missing (fall back to cache, badge stale). We never fabricate rows.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Final

import httpx

EIA_V2_BASE: Final[str] = "https://api.eia.gov/v2/petroleum/pri/gnd/data/"

# PADD → (district label, series_id suffix). We fetch sub-districts for
# PADD 1 so the Northeast map (1A/1B/1C) resolves correctly; others roll up.
PADD_SERIES_IDS: Final[dict[str, str]] = {
    "1A": "EMD_EPD2D_PTE_R1X_DPG",
    "1B": "EMD_EPD2D_PTE_R1Y_DPG",
    "1C": "EMD_EPD2D_PTE_R1Z_DPG",
    "2":  "EMD_EPD2D_PTE_R20_DPG",
    "3":  "EMD_EPD2D_PTE_R30_DPG",
    "4":  "EMD_EPD2D_PTE_R40_DPG",
    "5":  "EMD_EPD2D_PTE_R50_DPG",
}


class EIAError(RuntimeError):
    pass


@dataclass(frozen=True)
class DieselRow:
    padd: str
    week_of: date
    price_usd: Decimal


class EIAClient:
    def __init__(self, api_key: str, http: httpx.AsyncClient | None = None, *, timeout_s: float = 20.0) -> None:
        self._key = api_key
        self._http = http
        self._timeout = timeout_s

    async def _get_json(self, params: dict) -> dict:
        params = {**params, "api_key": self._key}
        if self._http is not None:
            r = await self._http.get(EIA_V2_BASE, params=params, timeout=self._timeout)
        else:
            async with httpx.AsyncClient(timeout=self._timeout) as c:
                r = await c.get(EIA_V2_BASE, params=params)
        if r.status_code >= 400:
            raise EIAError(f"EIA v2 returned {r.status_code}: {r.text[:200]}")
        try:
            return r.json()
        except ValueError as exc:
            raise EIAError(f"EIA v2 bad JSON: {exc}") from exc

    async def latest_by_padd(self) -> list[DieselRow]:
        """Return the most-recent weekly row for each PADD district."""
        out: list[DieselRow] = []
        for padd, series_id in PADD_SERIES_IDS.items():
            params = {
                "frequency": "weekly",
                "data[0]": "value",
                "facets[series][]": series_id,
                "sort[0][column]": "period",
                "sort[0][direction]": "desc",
                "length": 1,
            }
            payload = await self._get_json(params)
            rows = (payload or {}).get("response", {}).get("data") or []
            if not rows:
                continue
            row = rows[0]
            period = row.get("period")
            value = row.get("value")
            if not period or value is None:
                continue
            try:
                week_of = datetime.strptime(period, "%Y-%m-%d").replace(tzinfo=UTC).date()
                price = Decimal(str(value))
            except (ValueError, ArithmeticError):
                continue
            out.append(DieselRow(padd=padd, week_of=week_of, price_usd=price))
        return out


async def load_api_key(session) -> str | None:
    """Read the EIA API key from the vault; None when absent (no key saved
    yet). We never log the plaintext."""
    from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.tenant import TenantId

    try:
        vault = await CredentialVault.for_session(session)
    except VaultConfigError:
        return None
    try:
        bundle = await vault.get(session, TenantId(LJM_TENANT_ID), "eia", "api_key")
    except VaultNotFound:
        return None
    key = bundle.get("api_key") if isinstance(bundle, dict) else None
    return str(key) if key else None
