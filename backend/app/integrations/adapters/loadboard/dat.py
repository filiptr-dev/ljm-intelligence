"""DAT One API adapter — service-account two-step token exchange.

Field names marked ``# VERIFY-AT-ACCESS-DAY`` — DAT's dev-portal OpenAPI is
gated behind the service-account mail. The ``map_dat_row`` function + the
request body in :meth:`DatSource.fetch` are the two places to adjust when
the live spec lands.

Zero scraping: all vendor traffic is REST, auth is in-memory only, secrets
are only unwrapped at the HTTP boundary.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime

import httpx

from app.integrations.adapters.loadboard.base import ConnectionTest, RawLoad
from app.shared.circuit_breaker import CircuitBreaker

log = logging.getLogger(__name__)

# Module-level factory so tests can inject ``httpx.MockTransport``. Keeping it
# as a thin lambda (and not a direct ``httpx.AsyncClient`` reference) means
# monkeypatching only this symbol is enough — never need to touch the adapter.
_client_factory = lambda: httpx.AsyncClient()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).rstrip("Z"))
    except ValueError:
        return None


def map_dat_row(row: dict) -> RawLoad:
    # VERIFY-AT-ACCESS-DAY: DAT One search response field names.
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
        miles=int(miles) if isinstance(miles, int | float) else None,
        posted_at=_parse_dt(row.get("postedWhen")),
        raw=row,
    )


class DatSource:
    kind: str = "dat"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._org_token: tuple[str, float] | None = None  # (token, expires_monotonic)
        self._user_token: tuple[str, float] | None = None
        self._breaker = CircuitBreaker()

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(
            s.dat.service_account_email and s.dat.service_account_password and s.dat.org_id
        )

    def reason(self) -> str | None:
        if self._breaker.is_open():
            return "circuit_open"
        if self.enabled:
            return None
        missing = []
        s = self.settings
        if not s.dat.service_account_email:
            missing.append("DAT_SERVICE_ACCOUNT_EMAIL")
        if not s.dat.service_account_password:
            missing.append("DAT_SERVICE_ACCOUNT_PASSWORD")
        if not s.dat.org_id:
            missing.append("DAT_ORG_ID")
        return f"missing_env:{','.join(missing)}"

    async def _org_token_get(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._org_token and self._org_token[1] > now + 60:
            return self._org_token[0]
        s = self.settings
        resp = await client.post(
            f"{s.dat.base_url}/auth/v2/token/organization",
            json={
                "username": s.dat.service_account_email,
                "password": s.dat.service_account_password.get_secret_value(),
                "organizationId": s.dat.org_id,
            },
            timeout=httpx.Timeout(10.0, read=20.0),
        )
        resp.raise_for_status()
        body = resp.json() or {}
        token = str(body.get("accessToken") or body.get("token") or "")
        self._org_token = (token, now + 23 * 3600)
        return token

    async def _user_token_get(self, client: httpx.AsyncClient) -> str:
        # VERIFY-AT-ACCESS-DAY: DAT's two-step user-token exchange; TTL is
        # short-lived — we re-mint when within 60s of expiry.
        now = time.monotonic()
        if self._user_token and self._user_token[1] > now + 60:
            return self._user_token[0]
        s = self.settings
        org_token = await self._org_token_get(client)
        resp = await client.post(
            f"{s.dat.base_url}/auth/v2/token/user",
            json={"username": s.dat.service_account_email},
            headers={"Authorization": f"Bearer {org_token}"},
            timeout=httpx.Timeout(10.0, read=20.0),
        )
        resp.raise_for_status()
        body = resp.json() or {}
        token = str(body.get("accessToken") or body.get("token") or "")
        # TTL config knob; default a bit less than DAT's documented 1h ceiling.
        ttl = getattr(s.dat, "user_token_ttl_seconds", 3300)
        self._user_token = (token, now + float(ttl))
        return token

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        if self._breaker.is_open():
            return []
        s = self.settings
        try:
            async with _client_factory() as client:
                user_token = await self._user_token_get(client)
                # VERIFY-AT-ACCESS-DAY: DAT One search body + endpoint shape.
                resp = await client.post(
                    f"{s.dat.base_url}/search/v3/loads",
                    headers={"Authorization": f"Bearer {user_token}"},
                    json={
                        "equipment": None,
                        "limit": 100,
                    },
                    timeout=httpx.Timeout(10.0, read=20.0),
                )
                resp.raise_for_status()
                payload = resp.json() or {}
                # VERIFY-AT-ACCESS-DAY: response rows may be under "matches"
                # or "results" depending on DAT's SDK rev — accept both.
                rows = payload.get("matches") or payload.get("results") or []
                self._breaker.record_success()
                return [map_dat_row(r) for r in rows if isinstance(r, dict)]
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure(exc.response.status_code)
            log.warning(
                "dat/fetch",
                extra={"status_code": exc.response.status_code, "endpoint": "/search/v3/loads"},
            )
            return []
        except Exception as exc:  # noqa: BLE001 — network / timeout / parse
            self._breaker.record_failure(None)
            log.warning("dat/fetch: %s", type(exc).__name__)
            return []

    async def test_connection(self, settings) -> ConnectionTest:
        if not self.enabled:
            return ConnectionTest(ok=False, reason=self.reason())
        t0 = time.monotonic()
        try:
            async with _client_factory() as client:
                await self._user_token_get(client)
            return ConnectionTest(
                ok=True, latency_ms=int((time.monotonic() - t0) * 1000), sample_count=0
            )
        except Exception as exc:  # noqa: BLE001
            return ConnectionTest(
                ok=False,
                latency_ms=int((time.monotonic() - t0) * 1000),
                reason=type(exc).__name__,
            )


__all__ = ["DatSource", "map_dat_row"]
