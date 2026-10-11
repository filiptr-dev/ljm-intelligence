"""AbpClient — the ONLY path that speaks to LoadStop.

Strictly read-only. The ``call`` method refuses to open a socket for any
method that fails the allowlist regex *or* appears in the explicit write
denylist — belt-and-braces. Discovery flagged these as business-critical:
``TruckPosting`` / ``PostTruck`` / ``SyncLoads`` can post LJM trucks
publicly to DAT. A rename like ``GetAndDelete`` would pass the allowlist
on its own, which is why we triangulate with the denylist too.

SSRF: hostname is pinned to ``secure.loadstop.com`` — ``tracking.loadstop
.com`` is deliberately NOT reachable (discovery: user access denied; we
read truck locations from the ``trucks`` service instead).

Concurrency: one semaphore-of-1 per client — serial calls, polite vendor
load. Auto-re-login on 401 is bounded (one attempt, then raise).
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any, Callable, Awaitable

import httpx


LOG = logging.getLogger(__name__)

BASE_HOST = "secure.loadstop.com"
BASE_URL = f"https://{BASE_HOST}"
ALLOWED_HOSTS = frozenset({BASE_HOST})

# Allowlist — the method name must start with a known read verb. Keep this
# narrow; a leaked write would be catastrophic. Case-sensitive on purpose
# (ABP method names are PascalCase).
READ_ONLY_METHODS = re.compile(
    r"^(Get|GetAll|GetBy|List|Search|Export|Download|Count|Load|Fetch|Read|Preview)[A-Z]\w*$"
    r"|^(Get|GetAll|GetBy|List|Search|Export|Download|Count|Load|Fetch|Read|Preview)$"
)

# Denylist — explicit, in case a renamed write slips past the regex (e.g.
# ``GetAndDelete`` starts with ``Get`` but is a write). Also catches the
# dangerous outward-facing verbs discovery flagged by name.
WRITE_METHODS_DENYLIST = frozenset(
    {
        "Create",
        "Update",
        "Delete",
        "Post",
        "Sync",
        "Save",
        "Submit",
        "Send",
        "Upload",
        "Approve",
        "Reject",
        "Cancel",
        "Pause",
        "Resume",
        "Assign",
        "TruckPosting",
        "PostTruck",
        "PostTruckToDat",
        "SyncLoads",
        "Negotiate",
        "GetAndDelete",
        "GetAndUpdate",
        "GetAndCreate",
    }
)
# Prefix markers that unambiguously indicate a write even inside a longer
# method name (e.g. ``GetAndDelete``, ``TruckPostingSubmit``). Checked as
# a substring on the full method name after the allowlist regex.
WRITE_PREFIX_MARKERS = (
    "Delete",
    "Create",
    "Update",
    "Submit",
    "Post",
    "Save",
    "Send",
    "Upload",
    "Sync",
    "Approve",
    "Reject",
    "Cancel",
    "Negotiate",
    "Assign",
)


class LoadstopWriteBlocked(RuntimeError):
    """Raised BEFORE any socket is opened for a non-read method."""


class LoadstopSSRFBlocked(RuntimeError):
    """Raised when a host outside the allowlist is targeted."""


class LoadstopAuthRequired(RuntimeError):
    """Session cookie missing or expired; caller should re-login."""


def _is_read_only(method: str) -> bool:
    """Pure predicate — allowlist regex AND not denylisted AND no write marker."""
    if not isinstance(method, str) or not method:
        return False
    if method in WRITE_METHODS_DENYLIST:
        return False
    if not READ_ONLY_METHODS.match(method):
        return False
    # Catch a write verb buried inside a Get* name (``GetAndDelete``,
    # ``GetAndPostTruck``, etc.) — the regex alone would pass these.
    for marker in WRITE_PREFIX_MARKERS:
        if marker in method:
            return False
    return True


def _assert_allowed_host(url: str) -> None:
    try:
        host = httpx.URL(url).host
    except Exception as exc:  # noqa: BLE001
        raise LoadstopSSRFBlocked(f"invalid url: {exc}") from exc
    if host not in ALLOWED_HOSTS:
        raise LoadstopSSRFBlocked(f"host not in allowlist: {host!r}")


class AbpClient:
    """Thin async wrapper around httpx for ABP JSON calls.

    Not a public API — handed out from ``session.open_client(...)`` which
    attaches the vault-stored cookies and sets up the re-login callback.
    """

    def __init__(
        self,
        http: httpx.AsyncClient,
        *,
        relogin: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        self._http = http
        self._lock = asyncio.Semaphore(1)
        self._relogin = relogin
        self._relogin_attempts = 0

    @staticmethod
    def assert_read_only(method: str) -> None:
        """Public gate — raises BEFORE any network call. Mirrors call()."""
        if not _is_read_only(method):
            raise LoadstopWriteBlocked(
                f"loadstop method {method!r} is not read-only; refused before socket"
            )

    async def call(
        self,
        service: str,
        method: str,
        body: dict[str, Any] | None = None,
        *,
        query: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """POST /api/services/app/<service>/<method>. Read-only enforced."""
        self.assert_read_only(method)
        url = f"{BASE_URL}/api/services/app/{service}/{method}"
        _assert_allowed_host(url)
        async with self._lock:
            resp = await self._http.post(url, json=body or {}, params=query or None)
            if resp.status_code in (401, 302) and self._relogin and self._relogin_attempts == 0:
                self._relogin_attempts += 1
                await self._relogin()
                resp = await self._http.post(url, json=body or {}, params=query or None)
            if resp.status_code in (401, 302):
                raise LoadstopAuthRequired(f"auth failed for {service}/{method}")
            resp.raise_for_status()
            data = resp.json()
            # Polite pacing between pages — the plan calls for 300ms. We're
            # under the semaphore so this is a straight sleep.
            await asyncio.sleep(0.3)
            return data

    async def paged(
        self,
        service: str,
        method: str,
        *,
        page_size: int = 200,
        extra: dict[str, Any] | None = None,
    ):
        """Yield ABP ``items`` pages — stops when a page returns < page_size."""
        self.assert_read_only(method)
        skip = 0
        while True:
            body: dict[str, Any] = {"skipCount": skip, "maxResultCount": page_size}
            if extra:
                body.update(extra)
            data = await self.call(service, method, body)
            result = data.get("result") or data
            items = result.get("items") if isinstance(result, dict) else None
            items = items or []
            yield items
            if len(items) < page_size:
                return
            skip += page_size
