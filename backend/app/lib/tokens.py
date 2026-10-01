"""Unsubscribe token sign/verify helpers — shared by the api and service layers.

Lifted out of ``app/api/_auth.py`` so ``app/services/unsub_config.py`` can
import them without pulling the route layer along for the ride (reviewer
finding, 2026-10-01). Behaviour is byte-identical to the previous location —
this is a pure move. ``app/api/_auth.py`` re-exports for existing callers.

Constant-time compare via ``hmac.compare_digest`` — no timing oracle.
"""

from __future__ import annotations

import base64
import hmac


def sign_unsubscribe_token(contact_id: int, secret: str) -> str:
    """HMAC-SHA256(str(contact_id), secret) as URL-safe base64.

    The token is ``<contact_id>.<sig>`` so verification is O(1) — we don't have
    to scan the DB. The signature is unforgeable without the secret, so
    integer-id enumeration is closed.
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
