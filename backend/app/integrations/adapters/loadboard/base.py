"""LoadSource protocol + shared value types.

Rules every adapter obeys:

* ``enabled`` is a pure read of env vars — never a DB call.
* ``fetch()`` returns ``[]`` and surfaces errors via ``IngestStats`` — it never
  raises. A broken vendor must never kill the batch.
* Secrets are read inside the HTTP call only (``SecretStr.get_secret_value()``).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol


@dataclass
class RawLoad:
    source: str
    source_ref: str
    broker_name: str = ""
    broker_email: str | None = None
    broker_phone: str | None = None
    origin_city: str | None = None
    origin_state: str | None = None
    dest_city: str | None = None
    dest_state: str | None = None
    pickup_date: datetime | None = None
    equipment: str | None = None
    rate_usd: float | None = None
    miles: int | None = None
    posted_at: datetime | None = None
    raw: dict = field(default_factory=dict)


@dataclass
class ConnectionTest:
    ok: bool
    latency_ms: int = 0
    reason: str | None = None
    sample_count: int = 0


class LoadSource(Protocol):
    kind: str
    enabled: bool

    async def fetch(self, settings) -> list[RawLoad]: ...

    async def test_connection(self, settings) -> ConnectionTest: ...
