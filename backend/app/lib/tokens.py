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
    """Constant-time verify. Returns contact_id on success, None on invalid.

    Only verifies the legacy contact-id token (``<contact_id>.<sig>``). The
    email-keyed variant (``e.<b64url(email)>.<sig>``) is handled by
    :func:`verify_any_unsubscribe_token`.
    """
    if not token or "." not in token:
        return None
    id_str, sig = token.split(".", 1)
    # Reject the email variant so callers that still take the int-only path
    # don't accidentally see it as a bogus contact id.
    if id_str == "e":
        return None
    try:
        contact_id = int(id_str)
    except ValueError:
        return None
    expected = sign_unsubscribe_token(contact_id, secret).split(".", 1)[1]
    if not hmac.compare_digest(sig, expected):
        return None
    return contact_id


def sign_unsubscribe_email_token(email: str, secret: str) -> str:
    """HMAC-SHA256 token keyed by lowercased email, URL-safe.

    Format: ``e.<b64url(email)>.<sig>``. Used when an inbox-originated send
    has no matching ``lead_contacts`` row — the recipient must still be able
    to one-click unsubscribe (CAN-SPAM / RFC 8058), and the apply step
    records a ``suppression`` row keyed by email with no contact mutation.
    """
    email_norm = (email or "").strip().lower()
    payload_b64 = base64.urlsafe_b64encode(email_norm.encode()).decode("ascii").rstrip("=")
    msg = f"e.{payload_b64}".encode()
    sig = hmac.new(secret.encode(), msg, "sha256").digest()
    sig_b64 = base64.urlsafe_b64encode(sig).decode("ascii").rstrip("=")
    return f"e.{payload_b64}.{sig_b64}"


def _b64url_decode(payload: str) -> bytes:
    pad = "=" * (-len(payload) % 4)
    return base64.urlsafe_b64decode(payload + pad)


def verify_unsubscribe_email_token(token: str, secret: str) -> str | None:
    """Constant-time verify of the email-keyed token.

    Returns the normalised email on success, ``None`` on invalid / forged.
    """
    if not token or not token.startswith("e."):
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    _, payload_b64, sig = parts
    try:
        email_bytes = _b64url_decode(payload_b64)
    except (ValueError, Exception):  # noqa: BLE001 — malformed b64 → reject
        return None
    try:
        email = email_bytes.decode("utf-8")
    except UnicodeDecodeError:
        return None
    if not email or "@" not in email:
        return None
    expected = sign_unsubscribe_email_token(email, secret).split(".", 2)[2]
    if not hmac.compare_digest(sig, expected):
        return None
    return email


def verify_any_unsubscribe_token(token: str, secret: str) -> tuple[str, int | str] | None:
    """Verify either token flavour. Returns ``("contact", cid)`` or
    ``("email", normalised_email)`` on success; ``None`` on invalid.
    """
    if token and token.startswith("e."):
        em = verify_unsubscribe_email_token(token, secret)
        return ("email", em) if em is not None else None
    cid = verify_unsubscribe_token(token, secret)
    return ("contact", cid) if cid is not None else None
