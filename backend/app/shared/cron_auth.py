"""Shared route-auth helpers.

`check_secret(settings, provided)` is the same guard `/crawl/run` and
`/enrichment/auto-send` share — any route that triggers an outside-world
action (send email, kick off a crawl) must call it. The guard fails-open
in local dev (no `CRON_SECRET` set → logs a warning, allows the call);
in production, an unset secret is a warning, a mismatched one is a 401.

Constant-time compare via `hmac.compare_digest` — no timing oracle.

Unsubscribe token helpers live in :mod:`app.lib.tokens` so service-layer
modules (``app/services/unsub_config.py``) can import them without pulling
the route layer along. The names are re-exported from this module for
backwards compatibility — new callers should import ``sign_unsubscribe_token``
and ``verify_unsubscribe_token`` from ``app.lib.tokens`` directly.
"""

from __future__ import annotations

import hmac
import logging

from fastapi import HTTPException

from app.config import Settings
from app.shared.tokens import sign_unsubscribe_token, verify_unsubscribe_token

__all__ = ["check_secret", "sign_unsubscribe_token", "verify_unsubscribe_token"]

log = logging.getLogger(__name__)


def check_secret(settings: Settings, provided: str | None) -> None:
    expected = settings.cron_secret.get_secret_value() if settings.cron_secret else None
    if not expected:
        log.warning("CRON_SECRET not set — route is unauthenticated. Set it before deploying.")
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid cron secret")
