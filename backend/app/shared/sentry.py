"""Sentry wiring — optional, contextvar-tagged.

Design notes:

* ``init_sentry(settings)`` is a no-op when ``settings.sentry_dsn`` is unset.
  Local dev + CI stay off the network and never probe a DSN. In production
  the operator sets ``SENTRY_DSN`` on Render and the SDK boots in the
  FastAPI lifespan before any route is served.
* Every event is tagged with ``tenant_id`` and ``request_id`` through a
  ``before_send`` hook that reads the shared contextvars
  (``app.shared.logging._request_id_ctx``, ``app.shared.tenant._tenant_ctx``).
  Those vars are already bound by the ``RequestIdMiddleware`` and ``uow()``
  so the HTTP path, the ``run_crawl`` background path, and any future
  queue-worker job all get the right tag without extra plumbing.
* ``capture_exception`` is a thin wrapper that lets callers (notably the
  crawl pipeline's top-level ``except Exception``) report an exception
  without importing ``sentry_sdk`` directly — keeps the dependency to one
  module so tests can monkeypatch the surface.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

import sentry_sdk

if TYPE_CHECKING:
    from app.config import Settings

log = logging.getLogger(__name__)

_INITIALISED = False


def _before_send(event: dict[str, Any], hint: dict[str, Any]) -> dict[str, Any] | None:
    """Tag every outgoing event with request_id + tenant_id when bound."""
    try:
        from app.shared.logging import _job_id_ctx, _request_id_ctx
        from app.shared.tenant import _tenant_ctx

        tags = event.setdefault("tags", {})
        rid = _request_id_ctx.get()
        if rid:
            tags["request_id"] = rid
        jid = _job_id_ctx.get()
        if jid:
            tags["job_id"] = jid
        tid = _tenant_ctx.get()
        if tid:
            tags["tenant_id"] = tid
    except Exception:  # noqa: BLE001 — never let the tag hook kill a report.
        log.exception("sentry before_send tagging failed")
    return event


def init_sentry(settings: "Settings") -> bool:
    """Init the SDK iff a DSN is configured. Returns True when actually initialised."""
    global _INITIALISED
    if _INITIALISED:
        return True
    dsn = settings.sentry_dsn
    if not dsn:
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=settings.app_env,
        traces_sample_rate=settings.sentry_traces_sample_rate,
        before_send=_before_send,
        # FastAPI/Starlette integrations are auto-enabled by sentry-sdk when
        # the frameworks are importable; keep defaults.
    )
    _INITIALISED = True
    log.info("sentry initialised env=%s", settings.app_env)
    return True


def capture_exception(exc: BaseException) -> None:
    """Report an exception iff Sentry is wired. Safe to call unconditionally."""
    if not _INITIALISED:
        return
    sentry_sdk.capture_exception(exc)


def _reset_for_tests() -> None:
    """Test-only: clear the initialised flag so a monkeypatched init can run again."""
    global _INITIALISED
    _INITIALISED = False
