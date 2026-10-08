"""Single source of truth for the unsubscribe secret + public base URL.

The unsubscribe footer is always on: every outgoing email gets it, appended
by the send code itself. Nothing about it is user-configurable.

Secret
  * Env — ``Settings.unsubscribe_secret`` (SecretStr), ops override.
  * DB — ``SettingsRow.unsubscribe_secret``, seeded by migration 0007 and never
    patched through the public API.

Base URL
  * ``Settings.unsubscribe_base_url`` only — a fixed public URL with a code
    default (the Render API origin) and an optional env override. The legacy
    ``SettingsRow.unsubscribe_base_url`` column is no longer read, and the
    incoming request host is never used (behind Docker/a proxy it is an
    internal address, which would ship dead unsubscribe links).

If the link cannot be built (no secret anywhere, or a blank base URL), the send
path refuses with ``no_unsub_config`` and sends nothing.
"""

from __future__ import annotations

from typing import Protocol

from app.config import Settings
from app.shared.tokens import sign_unsubscribe_email_token, sign_unsubscribe_token


class _HasSecret(Protocol):
    unsubscribe_secret: str | None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def effective_secret(settings: Settings, row: _HasSecret | None) -> str | None:
    """HMAC secret, env → DB. ``None`` when neither side has a value."""
    env_secret = settings.unsubscribe_secret.get_secret_value() if settings.unsubscribe_secret else None
    return _clean(env_secret) or _clean(row.unsubscribe_secret if row else None)


def unsub_base_url(settings: Settings) -> str | None:
    """The fixed public base URL (code default, env override). No trailing slash."""
    base = _clean(settings.unsubscribe_base_url)
    return base.rstrip("/") if base else None


def effective_unsub(settings: Settings, row: _HasSecret | None) -> tuple[str | None, str | None]:
    """Return ``(secret, base_url)``."""
    return effective_secret(settings, row), unsub_base_url(settings)


def unsub_config_ready(settings: Settings, row: _HasSecret | None) -> bool:
    """True iff an unsubscribe link can be built."""
    secret, base_url = effective_unsub(settings, row)
    return bool(secret) and bool(base_url)


def unsub_missing_field(settings: Settings, row: _HasSecret | None) -> str | None:
    """Which piece is missing — for the ``unsub_config_missing`` detail."""
    secret, base_url = effective_unsub(settings, row)
    if not secret and not base_url:
        return "unsubscribe_base_url_and_secret"
    if not base_url:
        return "unsubscribe_base_url"
    if not secret:
        return "unsubscribe_secret"
    return None


def build_unsub_link(secret: str, base_url: str, contact_id: int) -> str:
    """Signed per-contact unsubscribe URL."""
    return f"{base_url.rstrip('/')}/unsubscribe?t={sign_unsubscribe_token(contact_id, secret)}"


def build_unsub_link_by_email(secret: str, base_url: str, email: str) -> str:
    """Signed email-keyed unsubscribe URL.

    Used by inbox-originated sends when no ``lead_contacts`` row exists for
    the recipient — the clicked link still resolves (CAN-SPAM / RFC 8058)
    and writes a ``suppression`` row keyed by email.
    """
    return f"{base_url.rstrip('/')}/unsubscribe?t={sign_unsubscribe_email_token(email, secret)}"


def with_unsub_footer(body: str, *, postal_address: str, unsub_url: str) -> str:
    """Append the mandatory CAN-SPAM footer (sender, postal address, unsubscribe
    link). Always applied by the send code; there is no way to turn it off."""
    return f"{body.rstrip()}\n\n— LJM International\n{postal_address}\nUnsubscribe: {unsub_url}\n"


def unsub_headers(unsub_url: str) -> dict[str, str]:
    """RFC 8058 one-click unsubscribe headers, always set on outgoing mail."""
    return {
        "List-Unsubscribe": f"<{unsub_url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }
