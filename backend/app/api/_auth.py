"""Shared route-auth helpers.

`_check_secret(settings, provided)` is the same guard `/crawl/run` and
`/enrichment/auto-send` share — any route that triggers an outside-world
action (send email, kick off a crawl) must call it. The guard fails-open
in local dev (no `CRON_SECRET` set → logs a warning, allows the call);
in production, an unset secret is a warning, a mismatched one is a 401.

Constant-time compare via `hmac.compare_digest` — no timing oracle.
"""

from __future__ import annotations

import base64
import hmac
import logging

from fastapi import HTTPException

from app.config import Settings

log = logging.getLogger(__name__)


def check_secret(settings: Settings, provided: str | None) -> None:
    expected = settings.cron_secret.get_secret_value() if settings.cron_secret else None
    if not expected:
        log.warning("CRON_SECRET not set — route is unauthenticated. Set it before deploying.")
        return
    if not provided or not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid cron secret")


# ----- unsubscribe token helpers --------------------------------------------


def sign_unsubscribe_token(contact_id: int, secret: str) -> str:
    """HMAC-SHA256(str(contact_id), secret) as URL-safe base64.

    The token is `<contact_id>.<sig>` so verification is O(1) — we don't have to
    scan the DB. The signature is unforgeable without the secret, so integer-id
    enumeration is closed.
    """
    msg = str(contact_id).encode()
    sig = hmac.new(secret.encode(), msg, "sha256").digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"{contact_id}.{sig_b64}"


def verify_unsubscribe_token(token: str, secret: str) -> int | None:
    """Constant-time verify. Returns contact_id on success, None on invalid."""
    if not token or "." not in token:
        return None
    id_str, sig = token.split(".", 1)
    try:
        contact_id = int(id_str)
    except ValueError:
        return None
    expected = sign_unsubscribe_token(contact_id, secret).split(".", 1)[1]
    if not hmac.compare_digest(sig, expected):
        return None
    return contact_id
