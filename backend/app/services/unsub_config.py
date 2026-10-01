"""Single source of truth for the effective unsubscribe secret + base URL.

Both values can live in two places:

  * Env vars — `Settings.unsubscribe_secret` (SecretStr) / `unsubscribe_base_url`
    (str). Optional; a `render.yaml` override lane for ops.
  * DB row — `SettingsRow.unsubscribe_secret` / `unsubscribe_base_url`, edited
    from the Settings UI (secret is seeded by migration 0007 and never patched
    through the public API).

`effective_unsub` prefers env → DB. That order is deliberate:

  * Preserves the current `render.yaml` contract — existing deploys keep
    working without a data migration on their DB.
  * Gives ops a break-glass override (rotate an env var, redeploy, done)
    without touching the DB.
  * DB values are the friendly path: first-time setup happens from Settings,
    no shell required.

The helper returns `(secret, base_url)`. A blank string on either side is
treated as "missing" — the UI writes `""` to clear a value.
"""

from __future__ import annotations

from app.config import Settings
from app.models import SettingsRow


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def effective_unsub(settings: Settings, row: SettingsRow | None) -> tuple[str | None, str | None]:
    """Return `(secret, base_url)` — env wins over DB; blank/None both mean missing."""
    env_secret = settings.unsubscribe_secret.get_secret_value() if settings.unsubscribe_secret else None
    secret = _clean(env_secret) or _clean(row.unsubscribe_secret if row else None)

    env_base = settings.unsubscribe_base_url
    base_url = _clean(env_base) or _clean(row.unsubscribe_base_url if row else None)

    return secret, base_url


def unsub_config_ready(settings: Settings, row: SettingsRow | None) -> bool:
    """True iff both effective sides resolve to a non-blank value."""
    secret, base_url = effective_unsub(settings, row)
    return bool(secret) and bool(base_url)


def unsub_missing_field(settings: Settings, row: SettingsRow | None) -> str | None:
    """Which knob(s) still need filling — for the 409 detail on arm."""
    secret, base_url = effective_unsub(settings, row)
    if not secret and not base_url:
        return "unsubscribe_base_url_and_secret"
    if not base_url:
        return "unsubscribe_base_url"
    if not secret:
        return "unsubscribe_secret"
    return None
