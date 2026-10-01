"""tiny robots.txt gate — one 24h-cached RobotFileParser per host.

Failures (unreachable robots, DNS error, timeout) default to *allowed*. We only
touch four low-volume pages per company (home, contact, about, team), and being
overly strict on a transient robots fetch would mask more contacts than it protects.
"""

from __future__ import annotations

import logging
import time
import urllib.request
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

# robots.txt fetches must never block the event loop forever. urllib's
# default has no socket timeout — if the host is unreachable, parser.read()
# will hang until the kernel gives up (minutes). Keep it short; failure is
# fail-open per the module doctrine.
_FETCH_TIMEOUT_S = 3.0

log = logging.getLogger(__name__)

_TTL_S = 24 * 60 * 60

# Sentinel: a host whose robots.txt is unreachable. Distinct from a cache
# miss, so we don't re-pay the 3s socket timeout on every URL in a crawl —
# previously the cache stored ``None`` for both "no entry" and "unreachable",
# which collapsed the states and made the cache a no-op for failing hosts.
_UNREACHABLE: object = object()

# host → (fetched_at_epoch, parser | _UNREACHABLE)
_CACHE: dict[str, tuple[float, object]] = {}


def _cache_get(host: str) -> object | None:
    """Return the cached value (parser or ``_UNREACHABLE``), or None on miss."""
    entry = _CACHE.get(host)
    if not entry:
        return None
    ts, cached = entry
    if (time.time() - ts) > _TTL_S:
        _CACHE.pop(host, None)
        return None
    return cached


def _fetch_parser(host: str, scheme: str) -> RobotFileParser | None:
    url = f"{scheme}://{host}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(url)
    try:
        # Replace parser.read() so we can bound the socket timeout — the
        # stdlib call has no default and will hang on an unreachable host.
        req = urllib.request.Request(url, headers={"User-Agent": "LJM-robots/1.0"})
        with urllib.request.urlopen(req, timeout=_FETCH_TIMEOUT_S) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
        parser.parse(raw.splitlines())
    except Exception as exc:  # noqa: BLE001 — network / timeout / parse
        log.info("robots: unreachable for %s: %s", host, exc)
        return None
    return parser


def is_allowed(url: str, user_agent: str) -> bool:
    """Return True when `user_agent` may fetch `url` per robots.txt (or on lookup failure)."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    scheme = parts.scheme or "https"
    if not host:
        return True
    cached = _cache_get(host)
    if cached is None:
        parser = _fetch_parser(host, scheme)
        # Store the sentinel on failure so we don't retry every URL for the
        # TTL — crawling 8 pages against one unreachable host used to burn 8
        # × _FETCH_TIMEOUT_S seconds.
        _CACHE[host] = (time.time(), parser if parser is not None else _UNREACHABLE)
        cached = parser if parser is not None else _UNREACHABLE
    if cached is _UNREACHABLE:
        # Unreachable robots — default to allowed (see module docstring).
        return True
    try:
        return cached.can_fetch(user_agent, url)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        return True


def clear_cache() -> None:
    """Test helper — drop the in-memory cache between test cases."""
    _CACHE.clear()
