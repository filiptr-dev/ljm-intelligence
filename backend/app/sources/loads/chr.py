"""C.H. Robinson Navisphere Carrier adapter — OAuth2 client-credentials.

Field mapping marked (verify at access day) — CHR portal docs are gated.
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


def map_chr_row(row: dict) -> RawLoad:
    origin = row.get("origin") or {}
    dest = row.get("destination") or {}
    contact = row.get("contact") or {}
    return RawLoad(
        source="chr",
        source_ref=str(row.get("load_id") or row.get("id") or ""),
        broker_name="C.H. Robinson",
        broker_email=contact.get("email"),
        broker_phone=contact.get("phone"),
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


class ChrSource:
    kind: str = "chr"

    def __init__(self, settings) -> None:
        self.settings = settings
        self._token: tuple[str, float] | None = None

    @property
    def enabled(self) -> bool:
        s = self.settings
        return bool(s.chr_client_id and s.chr_client_secret and s.chr_carrier_code)

    def reason(self) -> str | None:
        if self.enabled:
            return None
        missing = []
        s = self.settings
        if not s.chr_client_id:
            missing.append("CHR_CLIENT_ID")
        if not s.chr_client_secret:
            missing.append("CHR_CLIENT_SECRET")
        if not s.chr_carrier_code:
            missing.append("CHR_CARRIER_CODE")
        return f"missing_env:{','.join(missing)}"

    async def _token_get(self, client: httpx.AsyncClient) -> str:
        now = time.monotonic()
        if self._token and self._token[1] > now + 60:
            return self._token[0]
        s = self.settings
        resp = await client.post(
            f"{s.chr_base_url}/oauth2/v2.0/token",
            data={
                "grant_type": "client_credentials",
                "client_id": s.chr_client_id,
                "client_secret": s.chr_client_secret.get_secret_value(),
                "scope": s.chr_scope,
            },
            timeout=httpx.Timeout(10.0, read=20.0),
        )
        resp.raise_for_status()
        body = resp.json()
        token = str(body.get("access_token") or "")
        expires_in = int(body.get("expires_in") or 1800)
        self._token = (token, now + expires_in - 60)
        return token

    async def fetch(self, settings) -> list[RawLoad]:
        if not self.enabled:
            return []
        try:
            async with httpx.AsyncClient() as client:
                _ = await self._token_get(client)
                return []
        except Exception as exc:  # noqa: BLE001
            log.warning("chr/fetch: %s", exc)
            return []

    async def test_connection(self, settings) -> ConnectionTest:
        if not self.enabled:
            return ConnectionTest(ok=False, reason=self.reason())
        t0 = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                await self._token_get(client)
            return ConnectionTest(ok=True, latency_ms=int((time.monotonic() - t0) * 1000), sample_count=0)
        except Exception as exc:  # noqa: BLE001
            return ConnectionTest(ok=False, latency_ms=int((time.monotonic() - t0) * 1000), reason=str(exc))


__all__ = ["ChrSource", "map_chr_row"]
