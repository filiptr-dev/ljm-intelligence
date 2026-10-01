"""Truckstop Load Board Pro adapter — IntegrationId/User/Password headers.

(Verify at access day — Truckstop ships per-tenant docs after the SIA.)
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from app.lib.circuit_breaker import CircuitBreaker
from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad

log = logging.getLogger(__name__)


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def map_truckstop_row(row: dict) -> RawLoad:
    origin = row.get("origin") or {}
    dest = row.get("destination") or {}
    return RawLoad(
        source="truckstop",
        source_ref=str(row.get("loadId") or row.get("id") or ""),
        broker_name=str(row.get("postersCompanyName") or ""),
        broker_email=row.get("contactEmail"),
        broker_phone=row.get("contactPhone"),
        origin_city=origin.get("city"),
        origin_state=origin.get("stateProvince"),
        dest_city=dest.get("city"),
        dest_state=dest.get("stateProvince"),
        pickup_date=_parse_dt(row.get("pickupDate")),
        equipment=row.get("equipment"),
        rate_usd=row.get("rate"),
        miles=row.get("miles"),
        posted_at=_parse_dt(row.get("postedAt")),
        raw=row,
    )


class TruckstopSource:
    kind: str = "truckstop"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._breaker = CircuitBreaker()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.truckstop.integration_id and s.truckstop.username and s.truckstop.password)

    def reason(self) -> str | None:
        if self._breaker.is_open():
            return "circuit_open"
        if self.enabled:
            return None
        missing = []
        s = self.settings
        if not s.truckstop.integration_id:
            missing.append("TRUCKSTOP_INTEGRATION_ID")
        if not s.truckstop.username:
            missing.append("TRUCKSTOP_USERNAME")
        if not s.truckstop.password:
            missing.append("TRUCKSTOP_PASSWORD")
        return f"missing_env:{','.join(missing)}"

    def _headers(self) -> dict:
        s = self.settings
        return {
            "IntegrationId": s.truckstop.integration_id.get_secret_value(),
            "User": s.truckstop.username or "",
            "Password": s.truckstop.password.get_secret_value(),
        }

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        if self._breaker.is_open():
            return []
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.settings.truckstop.base_url}/loads/search",
                    params={"limit": 0},
                    headers=self._headers(),
                    timeout=httpx.Timeout(10.0, read=20.0),
                )
                resp.raise_for_status()
                self._breaker.record_success()
                return []
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure(exc.response.status_code)
            log.warning("truckstop/fetch: %s", exc)
            return []
        except Exception as exc:  # noqa: BLE001 — network / timeout / parse
            self._breaker.record_failure(None)
            log.warning("truckstop/fetch: %s", exc)
            return []

    async def test_connection(self, settings) -> ConnectionTest:
        if not self.enabled:
            return ConnectionTest(ok=False, reason=self.reason())
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self.settings.truckstop.base_url}/loads/search",
                    params={"limit": 1},
                    headers=self._headers(),
                    timeout=httpx.Timeout(10.0, read=20.0),
                )
                resp.raise_for_status()
            return ConnectionTest(ok=True, latency_ms=int((time.monotonic() - t0) * 1000), sample_count=0)
        except Exception as exc:  # noqa: BLE001
            return ConnectionTest(ok=False, latency_ms=int((time.monotonic() - t0) * 1000), reason=str(exc))


__all__ = ["TruckstopSource", "map_truckstop_row"]
