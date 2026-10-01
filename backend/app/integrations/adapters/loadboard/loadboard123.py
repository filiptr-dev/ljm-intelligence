"""123Loadboard adapter — API key header + session login.

Field mapping marked ``# VERIFY-AT-ACCESS-DAY`` — 123LB publishes the full
spec after the API agreement is executed.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad
from app.lib.circuit_breaker import CircuitBreaker

log = logging.getLogger(__name__)

_client_factory = lambda: httpx.AsyncClient()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def map_lb123_row(row: dict) -> RawLoad:
    # VERIFY-AT-ACCESS-DAY: 123Loadboard search response field names.
    broker = row.get("broker") or {}
    origin = row.get("origin") or {}
    dest = row.get("destination") or {}
    return RawLoad(
        source="loadboard123",
        source_ref=str(row.get("load_id") or row.get("id") or ""),
        broker_name=str(broker.get("company_name") or ""),
        broker_email=broker.get("email"),
        broker_phone=broker.get("phone"),
        origin_city=origin.get("city"),
        origin_state=origin.get("state"),
        dest_city=dest.get("city"),
        dest_state=dest.get("state"),
        pickup_date=_parse_dt(row.get("pickup_date")),
        equipment=row.get("equipment"),
        rate_usd=row.get("rate_usd"),
        miles=row.get("miles"),
        posted_at=_parse_dt(row.get("posted_at")),
        raw=row,
    )


class LoadBoard123Source:
    kind: str = "loadboard123"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._session: tuple[str, float] | None = None
        self._breaker = CircuitBreaker()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.lb123.api_key and s.lb123.carrier_username and s.lb123.carrier_password)

    def reason(self) -> str | None:
        if self._breaker.is_open():
            return "circuit_open"
        if self.enabled:
            return None
        missing = []
        s = self.settings
        if not s.lb123.api_key:
            missing.append("LB123_API_KEY")
        if not s.lb123.carrier_username:
            missing.append("LB123_CARRIER_USERNAME")
        if not s.lb123.carrier_password:
            missing.append("LB123_CARRIER_PASSWORD")
        return f"missing_env:{','.join(missing)}"

    async def _login(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._session and self._session[1] > now + 60:
            return self._session[0]
        s = self.settings
        headers = {"X-API-Key": s.lb123.api_key.get_secret_value()}
        resp = await client.post(
            f"{s.lb123.base_url}/auth/login",
            headers=headers,
            json={
                "username": s.lb123.carrier_username,
                "password": s.lb123.carrier_password.get_secret_value(),
            },
            timeout=httpx.Timeout(10.0, read=20.0),
        )
        resp.raise_for_status()
        body = resp.json() or {}
        token = str(body.get("session_token") or body.get("token") or "")
        # VERIFY-AT-ACCESS-DAY: 123LB session TTL — docs quote 30 min default.
        expires_in = int(body.get("expires_in") or 1800)
        self._session = (token, now + expires_in - 60)
        return token

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        if self._breaker.is_open():
            return []
        s = self.settings
        try:
            async with _client_factory() as client:
                session = await self._login(client)
                # VERIFY-AT-ACCESS-DAY: 123LB search path + auth header.
                resp = await client.get(
                    f"{s.lb123.base_url}/loads/search",
                    headers={
                        "X-API-Key": s.lb123.api_key.get_secret_value(),
                        "Authorization": f"Bearer {session}",
                    },
                    params={"limit": 100},
                    timeout=httpx.Timeout(10.0, read=20.0),
                )
                resp.raise_for_status()
                payload = resp.json() or {}
                rows = payload.get("loads") or payload.get("results") or []
                self._breaker.record_success()
                return [map_lb123_row(r) for r in rows if isinstance(r, dict)]
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure(exc.response.status_code)
            log.warning(
                "loadboard123/fetch",
                extra={"status_code": exc.response.status_code, "endpoint": "/loads/search"},
            )
            return []
        except Exception as exc:  # noqa: BLE001 — network / timeout / parse
            self._breaker.record_failure(None)
            log.warning("loadboard123/fetch: %s", type(exc).__name__)
            return []

    async def test_connection(self, settings) -> ConnectionTest:
        if not self.enabled:
            return ConnectionTest(ok=False, reason=self.reason())
        t0 = time.monotonic()
        try:
            async with _client_factory() as client:
                await self._login(client)
            return ConnectionTest(ok=True, latency_ms=int((time.monotonic() - t0) * 1000), sample_count=0)
        except Exception as exc:  # noqa: BLE001
            return ConnectionTest(ok=False, latency_ms=int((time.monotonic() - t0) * 1000), reason=type(exc).__name__)


__all__ = ["LoadBoard123Source", "map_lb123_row"]
