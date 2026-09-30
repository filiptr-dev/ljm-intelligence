"""tiny robots.txt gate — one 24h-cached RobotFileParser per host.

Failures (unreachable robots, DNS error, timeout) default to *allowed*. We only
touch four low-volume pages per company (home, contact, about, team), and being
overly strict on a transient robots fetch would mask more contacts than it protects.
"""

from __future__ import annotations

import logging
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

log = logging.getLogger(__name__)

_TTL_S = 24 * 60 * 60
# host → (fetched_at_epoch, parser | None)
_CACHE: dict[str, tuple[float, RobotFileParser | None]] = {}


def _cache_get(host: str) -> RobotFileParser | None:
    entry = _CACHE.get(host)
    if not entry:
        return None
    ts, parser = entry
    if (time.time() - ts) > _TTL_S:
        _CACHE.pop(host, None)
        return None
    return parser


def _fetch_parser(host: str, scheme: str) -> RobotFileParser | None:
    url = f"{scheme}://{host}/robots.txt"
    parser = RobotFileParser()
    parser.set_url(url)
    try:
        parser.read()
    except Exception as exc:  # noqa: BLE001
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
    parser = _cache_get(host)
    if parser is None:
        parser = _fetch_parser(host, scheme)
        _CACHE[host] = (time.time(), parser)
    if parser is None:
        # Unreachable robots — default to allowed (see docstring).
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:  # noqa: BLE001
        return True


def clear_cache() -> None:
    """Test helper — drop the in-memory cache between test cases."""
    _CACHE.clear()
