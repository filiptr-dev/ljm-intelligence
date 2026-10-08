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


# Process-local vault-resolved cache. Primed by
# ``prime_from_vault(sessionmaker)`` during app lifespan startup, so the
# sync factory functions (``get_mailbox_source``, ``get_mail_sender``) can
# consult it with zero extra async plumbing. Env still wins — the cache is
# only consulted when ``settings.gmail.sa_json`` is empty.
_vault_sa_cache: dict[str, Any] | None = None
_vault_impersonate_cache: str | None = None


def set_vault_cache(sa_json: str | None, impersonate: str | None) -> None:
    """Internal — install the vault-resolved SA JSON in the process cache."""
    global _vault_sa_cache, _vault_impersonate_cache
    _vault_sa_cache = load_sa_info(sa_json)
    _vault_impersonate_cache = (impersonate or "").strip() or None


def reset_vault_cache() -> None:
    """Test hook."""
    global _vault_sa_cache, _vault_impersonate_cache
    _vault_sa_cache = None
    _vault_impersonate_cache = None


def vault_sa() -> dict[str, Any] | None:
    return _vault_sa_cache


def vault_impersonate() -> str | None:
    return _vault_impersonate_cache


async def prime_from_vault(sessionmaker: Any) -> None:
    """Load Gmail SA JSON from the vault into the module cache.

    Called from app lifespan startup — no-op when the vault key is
    missing or no credential has been stored yet. Idempotent.
    """
    try:
        from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound
        from app.shared.orm import LJM_TENANT_ID
        from app.shared.tenant import TenantId
    except Exception as exc:  # pragma: no cover
        log.debug("mail/credentials: vault prime skipped (%s)", exc)
        return
    try:
        async with sessionmaker() as s:
            try:
                vault = await CredentialVault.for_session(s)
            except VaultConfigError:
                return
            try:
                bundle = await vault.get(s, TenantId(LJM_TENANT_ID), "gmail", "service_account")
            except VaultNotFound:
                return
            set_vault_cache(
                str(bundle.get("sa_json") or ""),
                str(bundle.get("impersonate") or ""),
            )
    except Exception as exc:  # noqa: BLE001  pragma: no cover
        log.warning("mail/credentials: vault prime failed: %s", exc)


def resolve_sa_info(settings: Any) -> dict[str, Any] | None:
    """Env wins, else vault cache. Returns a parsed SA info dict or None."""
    env_sa = None
    try:
        env_sa = settings.gmail.sa_json.get_secret_value() if settings.gmail.sa_json else None
    except Exception:
        env_sa = None
    if env_sa:
        parsed = load_sa_info(env_sa)
        if parsed is not None:
            return parsed
    return _vault_sa_cache


def resolve_impersonate(settings: Any) -> str:
    """Env wins, else vault cache. Returns the admin-impersonate email."""
    try:
        env_imp = (getattr(settings.gmail, "admin_impersonate", "") or "").strip()
    except Exception:
        env_imp = ""
    if env_imp:
        return env_imp
    return _vault_impersonate_cache or ""


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
