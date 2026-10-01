"""Service-account credential loading + delegated-impersonation helpers.

Pure-stdlib JSON parse up front, so a bad ``GMAIL_SA_JSON`` surfaces as
``None`` rather than a stack trace at import time. Actual Google library
imports happen lazily inside ``build_delegated_credentials`` so test suites
and the simulated code path never pay the import cost.
"""

from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

log = logging.getLogger(__name__)


def load_sa_info(sa_json: str | None) -> dict[str, Any] | None:
    """Parse the service-account JSON string. Return ``None`` on empty/invalid."""
    if not sa_json:
        return None
    try:
        info = json.loads(sa_json)
    except (TypeError, ValueError) as exc:
        log.warning("mail/credentials: GMAIL_SA_JSON failed to parse: %s", exc)
        return None
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        log.warning("mail/credentials: GMAIL_SA_JSON missing client_email/private_key")
        return None
    return info


def sa_fingerprint(sa_info: dict[str, Any]) -> str:
    """Last 12 chars of sha256(client_id). Never leaks the key itself."""
    cid = str(sa_info.get("client_id") or sa_info.get("client_email") or "")
    return hashlib.sha256(cid.encode()).hexdigest()[-12:]


def build_delegated_credentials(sa_info: dict[str, Any], subject: str, scopes: list[str]):
    """Wrap google-auth's ``service_account.Credentials.from_service_account_info``.

    Returns ``None`` and logs when google-auth is not installed — this lets
    tests exercise the simulated code path without the dependency.
    """
    try:
        from google.oauth2 import service_account  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover - optional dependency
        log.warning("mail/credentials: google-auth not installed; cannot build credentials")
        return None
    creds = service_account.Credentials.from_service_account_info(sa_info, scopes=scopes)
    return creds.with_subject(subject)


def test_refresh(creds) -> tuple[bool, str | None]:
    """Force a token refresh. Returns ``(ok, reason)``."""
    if creds is None:
        return False, "no_credentials"
    try:
        from google.auth.transport.requests import Request  # type: ignore[import-not-found]
    except ImportError:  # pragma: no cover
        return False, "google_auth_not_installed"
    try:
        creds.refresh(Request())
        return True, None
    except Exception as exc:  # noqa: BLE001
        return False, f"refresh_failed: {exc}"
