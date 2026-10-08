"""OpenStreetMap Overpass source — one query per in-region state.

Endpoint: https://overpass-api.de/api/interpreter (no key). Overpass has a
usage policy: polite behaviour is required (User-Agent, low request rate,
cache). We keep to ≤1 req / 2s per client, a 24h on-disk cache keyed on
`(state, query_hash)`, a 30s timeout, and a graceful failure path — an
Overpass outage must NOT fail the whole crawl. See plan Slice 2b.

Tags we pull:
    - `industrial=distribution_centre`
    - `building=warehouse`

Named rows keep their name. **Unnamed** rows are kept ONLY for those two
"clearly a shipping facility" tags, with a synthesized label
`"Unnamed warehouse near <city>"` where `<city>` comes from `addr:city` (or
the state code as a fallback). Generic `landuse=industrial` polygons without
a name would be too noisy and are deliberately dropped upstream by not
including that tag in the query.

Tests mock this module by passing a pre-built httpx.AsyncClient with a
MockTransport — no network in CI. See `tests/test_osm_overpass.py`.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

import httpx

from app.prospecting.pipeline.shipper_merge import IncomingCandidate
from app.shared.region import bbox_for_state

log = logging.getLogger(__name__)

OVERPASS_URL = "https://overpass-api.de/api/interpreter"
USER_AGENT = "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"
DEFAULT_TIMEOUT = 30.0
# Overpass usage policy: no more than ~1 request every 2 seconds per client.
POLITE_INTERVAL_SECONDS = 2.0
CACHE_TTL_SECONDS = 24 * 60 * 60  # 24h
DEFAULT_CACHE_DIR = Path(__file__).resolve().parent.parent.parent / ".cache" / "overpass"


# Overpass QL — one query per state, bounded by that state's bbox.
# Uses `out center` so ways/relations come back with a representative point
# (`center: {lat, lon}`) — we don't want to reconstruct polygons here.
_OVERPASS_QL = (
    "[out:json][timeout:25];"
    "("
    'node["industrial"="distribution_centre"]({bbox});'
    'way["industrial"="distribution_centre"]({bbox});'
    'relation["industrial"="distribution_centre"]({bbox});'
    'node["building"="warehouse"]({bbox});'
    'way["building"="warehouse"]({bbox});'
    'relation["building"="warehouse"]({bbox});'
    ");"
    "out center tags;"
)


@dataclass(frozen=True, slots=True)
class OverpassElement:
    """One node/way/relation returned by Overpass, normalized.

    Kept as a dataclass so the ingest layer doesn't have to know Overpass's
    raw JSON shape. `ref` is `"<type>/<id>"` for use as the DB `osm_ref`.
    """

    ref: str  # "node/1" | "way/2" | "relation/3"
    lat: float | None
    lng: float | None
    tags: dict


# Simple in-process throttle. Overpass counts against a single origin;
# a global monotonic timestamp is enough for our modest per-run volume.
_last_request_at: float = 0.0
_throttle_lock = asyncio.Lock()


async def _throttle() -> None:
    """Sleep so that at least POLITE_INTERVAL_SECONDS have passed since the last request."""
    global _last_request_at
    async with _throttle_lock:
        now = time.monotonic()
        wait = POLITE_INTERVAL_SECONDS - (now - _last_request_at)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_request_at = time.monotonic()


def _cache_key(state: str, query: str) -> str:
    h = hashlib.sha256(query.encode("utf-8")).hexdigest()[:16]
    return f"{state.upper()}-{h}"


def _cache_path(cache_dir: Path, state: str, query: str) -> Path:
    return cache_dir / f"{_cache_key(state, query)}.json"


def _read_cache(path: Path, ttl_seconds: int) -> dict | None:
    try:
        st = path.stat()
    except OSError:
        return None
    age = time.time() - st.st_mtime
    if age > ttl_seconds:
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def _write_cache(path: Path, payload: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(payload, f)
        tmp.replace(path)
    except OSError:
        log.warning("overpass: failed to write cache %s", path, exc_info=True)


def _element_to_overpass(el: dict) -> OverpassElement | None:
    """Normalize one raw Overpass JSON element. Returns None on unusable rows."""
    t = el.get("type")
    oid = el.get("id")
    if not t or oid is None:
        return None
    tags = el.get("tags") or {}
    if not isinstance(tags, dict):
        tags = {}
    lat = el.get("lat")
    lng = el.get("lon")
    if lat is None or lng is None:
        center = el.get("center") or {}
        lat = center.get("lat")
        lng = center.get("lon")
    return OverpassElement(
        ref=f"{t}/{oid}",
        lat=float(lat) if lat is not None else None,
        lng=float(lng) if lng is not None else None,
        tags=tags,
    )


def _label_and_name(tags: dict, state: str) -> tuple[str, bool]:
    """Return (label, is_named). Synthesizes 'Unnamed warehouse near <city>' when unnamed."""
    name = (tags.get("name") or "").strip()
    if name:
        return name, True
    city = (tags.get("addr:city") or "").strip() or state.upper()
    return f"Unnamed warehouse near {city}", False


def build_overpass_query(state: str) -> str | None:
    """Build the OQL body for one state, or None if we don't have a bbox."""
    bbox = bbox_for_state(state)
    if not bbox:
        return None
    south, west, north, east = bbox
    return _OVERPASS_QL.format(bbox=f"{south},{west},{north},{east}")


async def fetch_overpass_elements(
    state: str,
    *,
    client: httpx.AsyncClient | None = None,
    cache_dir: Path | None = None,
    cache_ttl_seconds: int = CACHE_TTL_SECONDS,
    throttle: bool = True,
    raise_on_error: bool = False,
) -> list[OverpassElement]:
    """Fetch Overpass elements for `state`. Cached, throttled, graceful.

    Default behaviour returns [] on any failure (HTTP error, network error,
    JSON parse error, unknown state) — the crawler must never fail because
    Overpass is having a bad day. Set `raise_on_error=True` when the caller
    wants to *count* those failures (plan slice 3 — `osm_states_failed` /
    `osm_status`): all HTTP / network / JSON-parse failures are re-raised as
    the original `httpx.HTTPError` or `ValueError`. An unknown state (no bbox)
    still returns [] — it's a config issue, not a fetch failure.

    `client` can be a pre-built AsyncClient (with e.g. a MockTransport for
    tests). If None, a fresh client is created per call.
    """
    query = build_overpass_query(state)
    if query is None:
        log.debug("overpass: no bbox for %s; skipping", state)
        return []

    cache_dir = cache_dir or DEFAULT_CACHE_DIR
    cpath = _cache_path(cache_dir, state, query)
    cached = _read_cache(cpath, cache_ttl_seconds)
    if cached is not None:
        log.debug("overpass: cache hit for %s", state)
        return [e for e in (_element_to_overpass(x) for x in cached.get("elements") or []) if e]

    if throttle:
        await _throttle()

    owns_client = client is None
    if owns_client:
        client = httpx.AsyncClient(timeout=DEFAULT_TIMEOUT, headers={"User-Agent": USER_AGENT})
    try:
        assert client is not None
        resp = await client.post(
            OVERPASS_URL,
            data={"data": query},
            headers={"User-Agent": USER_AGENT},
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        log.warning("overpass: fetch failed for %s: %s", state, exc)
        if raise_on_error:
            raise
        return []
    finally:
        if owns_client:
            await client.aclose()

    if not isinstance(payload, dict):
        log.warning("overpass: unexpected payload shape for %s (not a dict)", state)
        if raise_on_error:
            raise ValueError(f"overpass: unexpected payload shape for {state} (not a dict)")
        return []

    _write_cache(cpath, payload)

    elements = payload.get("elements") or []
    out: list[OverpassElement] = []
    for raw in elements:
        el = _element_to_overpass(raw)
        if el is not None:
            out.append(el)
    return out


def element_to_incoming(el: OverpassElement, state: str) -> IncomingCandidate | None:
    """Map one Overpass element to an IncomingCandidate the merge fn accepts.

    Applies the plan's "keep unnamed only for distribution_centre / building=warehouse"
    rule (both tags already required by our OQL; a third-party cached payload could
    still contain other tags, so we defensively re-check here).

    Returns None if the element lacks both the required tag AND a name — belt
    and suspenders for cached responses.
    """
    tags = el.tags
    is_dc = tags.get("industrial") == "distribution_centre"
    is_warehouse = tags.get("building") == "warehouse"
    if not (is_dc or is_warehouse):
        return None

    label, _ = _label_and_name(tags, state)

    # City: prefer addr:city, else None (we don't guess).
    city = (tags.get("addr:city") or "").strip() or None
    # Best-effort address one-liner from OSM tags.
    parts: list[str] = []
    for k in ("addr:housenumber", "addr:street", "addr:postcode"):
        v = (tags.get(k) or "").strip()
        if v:
            parts.append(v)
    address = " ".join(parts) or None

    # Domain / phone straight off tags when present.
    website = (tags.get("website") or tags.get("contact:website") or "").strip() or None
    domain: str | None = None
    if website:
        # Strip scheme + path — the merge fn's _normalize_domain does the rest.
        d = website.split("//", 1)[-1].split("/", 1)[0]
        domain = d or None
    phone = (tags.get("phone") or tags.get("contact:phone") or "").strip() or None

    return IncomingCandidate(
        source="OSM",
        name=label,
        state=state.upper(),
        city=city,
        osm_ref=el.ref,
        phone=phone,
        domain=domain,
        extra={
            "tags": tags,
            "lat": el.lat,
            "lng": el.lng,
            "address": address,
        },
    )


def elements_to_incomings(elements: Iterable[OverpassElement], state: str) -> list[IncomingCandidate]:
    return [c for c in (element_to_incoming(e, state) for e in elements) if c is not None]
