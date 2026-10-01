"""DAT One API adapter — service-account two-step token exchange.

Field names marked (verify at access day) — DAT's dev-portal OpenAPI is gated
behind the service-account mail. The ``map_dat_row`` function is the one place
to adjust when the live spec lands.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from app.sources.loads.base import ConnectionTest, RawLoad

log = logging.getLogger(__name__)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def map_dat_row(row: dict) -> RawLoad:
    origin = row.get("origin") or {}
    dest = row.get("destination") or {}
    contact = row.get("contact") or {}
    posters = row.get("postersCompany") or {}
    rate_info = row.get("rateInfo") or {}
    trip = row.get("tripDistance") or {}
    avail = row.get("availability") or {}
    miles = trip.get("miles")
    return RawLoad(
        source="dat",
        source_ref=str(row.get("matchId") or row.get("id") or ""),
        broker_name=str(posters.get("name") or ""),
        broker_email=contact.get("email"),
        broker_phone=contact.get("phone"),
        origin_city=origin.get("city"),
        origin_state=origin.get("stateProv"),
        dest_city=dest.get("city"),
        dest_state=dest.get("stateProv"),
        pickup_date=_parse_dt(avail.get("earliestWhen")),
        equipment=row.get("equipmentType"),
        rate_usd=rate_info.get("rateUsd"),
        miles=int(miles) if isinstance(miles, (int, float)) else None,
        posted_at=_parse_dt(row.get("postedWhen")),
        raw=row,
    )


class DatSource:
    kind: str = "dat"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._org_token: tuple[str, float] | None = None  # (token, expires_monotonic)

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.dat_service_account_email and s.dat_service_account_password and s.dat_org_id)

    def reason(self) -> str | None:
        if self.enabled:
            return None
        missing = []
        s = self.settings
        if not s.dat_service_account_email:
            missing.append("DAT_SERVICE_ACCOUNT_EMAIL")
        if not s.dat_service_account_password:
            missing.append("DAT_SERVICE_ACCOUNT_PASSWORD")
        if not s.dat_org_id:
            missing.append("DAT_ORG_ID")
        return f"missing_env:{','.join(missing)}"

    async def _org_token_get(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._org_token and self._org_token[1] > now + 60:
            return self._org_token[0]
        s = self.settings
        resp = await client.post(
            f"{s.dat_base_url}/auth/v2/token/organization",
            json={
                "username": s.dat_service_account_email,
                "password": s.dat_service_account_password.get_secret_value(),
                "organizationId": s.dat_org_id,
            },
            timeout=httpx.Timeout(10.0, read=20.0),
        )
        resp.raise_for_status()
        token = str(resp.json().get("accessToken") or resp.json().get("token") or "")
        self._org_token = (token, now + 23 * 3600)
        return token

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        try:
            async with httpx.AsyncClient() as client:
                _ = await self._org_token_get(client)
                return []  # live search body goes here on access day
        except Exception as exc:  # noqa: BLE001
            log.warning("dat/fetch: %s", exc)
            return []

    async def test_connection(self, settings) -> ConnectionTest:
        if not self.enabled:
            return ConnectionTest(ok=False, reason=self.reason())
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                await self._org_token_get(client)
            return ConnectionTest(ok=True, latency_ms=int((time.monotonic() - t0) * 1000), sample_count=0)
        except Exception as exc:  # noqa: BLE001
            return ConnectionTest(ok=False, latency_ms=int((time.monotonic() - t0) * 1000), reason=str(exc))


__all__ = ["DatSource", "map_dat_row"]
