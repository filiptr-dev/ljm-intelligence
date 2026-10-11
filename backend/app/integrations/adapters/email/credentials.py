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
# Stored ``inbox.source`` TenantFeatureFlag — "gmail" | "simulated" | None.
# Primed from the DB at startup and refreshed in-process whenever the owner
# connects / disconnects via Settings so /mail/status reflects it with no
# restart. Env default ``mailbox_source=simulated`` loses to this cache when
# it says ``gmail``; an explicit env ``mailbox_source=gmail`` still wins.
_vault_inbox_source_cache: str | None = None
# Mail-read allowlist (migration 0044). ``None`` = no restriction. Env
# ``MAIL_READ_ALLOWLIST`` (CSV) wins over this cache. Primed from the
# ``settings.mail_read_allowlist`` JSONB column at app lifespan + worker
# bootstrap.
_vault_allowlist_cache: list[str] | None = None


def set_vault_cache(sa_json: str | None, impersonate: str | None) -> None:
    """Internal — install the vault-resolved SA JSON in the process cache."""
    global _vault_sa_cache, _vault_impersonate_cache
    _vault_sa_cache = load_sa_info(sa_json)
    _vault_impersonate_cache = (impersonate or "").strip() or None


def set_vault_inbox_source_cache(value: str | None) -> None:
    """Install the stored ``inbox.source`` flag in the process cache."""
    global _vault_inbox_source_cache
    v = (value or "").strip().lower() or None
    _vault_inbox_source_cache = v if v in ("gmail", "simulated") else None


def set_vault_allowlist_cache(values: list[str] | None) -> None:
    """Install the stored mail-read allowlist in the process cache.

    ``None`` or empty list disables the restriction (callers see ``None``
    and the ``list_mailboxes`` fan-out keeps its historical behaviour).
    """
    global _vault_allowlist_cache
    if not values:
        _vault_allowlist_cache = None
        return
    norm = [str(v).strip().lower() for v in values if str(v).strip()]
    _vault_allowlist_cache = norm or None


def reset_vault_cache() -> None:
    """Test hook."""
    global _vault_sa_cache, _vault_impersonate_cache, _vault_inbox_source_cache
    global _vault_allowlist_cache
    _vault_sa_cache = None
    _vault_impersonate_cache = None
    _vault_inbox_source_cache = None
    _vault_allowlist_cache = None


def vault_sa() -> dict[str, Any] | None:
    return _vault_sa_cache


async def ensure_vault_primed(sessionmaker: Any) -> None:
    """Prime the vault cache if the running process has never done so.

    The FastAPI app primes at lifespan startup, but the procrastinate worker
    (``python -m procrastinate … worker``) never goes through that lifespan
    — so without this lazy prime the worker's ``_vault_sa_cache`` stays
    ``None``, ``get_mailbox_source`` falls through to
    ``SimulatedMailbox(messages={})``, and every queued ``mail_backfill`` /
    ``mail_incremental`` returns 0 messages while silently never calling
    Gmail. Call this from every job that reads mail.

    Idempotent — once the cache has been populated by any path (lifespan,
    connect route, previous prime), this is a cheap no-op.
    """
    if (
        _vault_sa_cache is not None
        or _vault_inbox_source_cache is not None
        or _vault_allowlist_cache is not None
    ):
        return
    await prime_from_vault(sessionmaker)


def vault_impersonate() -> str | None:
    return _vault_impersonate_cache


def vault_inbox_source() -> str | None:
    return _vault_inbox_source_cache


def vault_allowlist() -> list[str] | None:
    return _vault_allowlist_cache


async def prime_from_vault(sessionmaker: Any) -> None:
    """Load Gmail SA JSON from the vault into the module cache.

    Called from app lifespan startup — no-op when the vault key is
    missing or no credential has been stored yet. Idempotent.
    """
    try:
        from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound
        from app.identity.models import TenantFeatureFlag
        from app.shared.orm import LJM_TENANT_ID
        from app.shared.tenant import TenantId
    except ImportError as exc:  # pragma: no cover
        log.debug("mail/credentials: vault prime skipped (%s)", exc)
        return
    try:
        from sqlalchemy import select as _sel, text as _text

        async with sessionmaker() as s:
            # Mirror the request-time binding so the vault read sees the same
            # RLS context as the connect route. The policy already allows
            # NULL/'', but a prod regression that forced policy-strict mode
            # (or a future per-tenant policy) would otherwise silently hide
            # the credential row at startup. Postgres only; sqlite ignores.
            try:
                bind = s.get_bind() if hasattr(s, "get_bind") else None
                dialect = getattr(getattr(bind, "dialect", None), "name", "")
                if dialect == "postgresql":
                    await s.execute(
                        _text("SELECT set_config('app.tenant_id', :tid, true)"),
                        {"tid": LJM_TENANT_ID},
                    )
            except Exception as exc:  # noqa: BLE001
                log.warning("mail/credentials: tenant GUC bind failed: %s", exc)
            try:
                vault = await CredentialVault.for_session(s)
            except VaultConfigError as exc:
                log.warning("mail/credentials: vault key unavailable at startup: %s", exc)
                vault = None
            if vault is not None:
                try:
                    bundle = await vault.get(
                        s, TenantId(LJM_TENANT_ID), "gmail", "service_account"
                    )
                    set_vault_cache(
                        str(bundle.get("sa_json") or ""),
                        str(bundle.get("impersonate") or ""),
                    )
                    log.info(
                        "mail/credentials: primed Gmail SA from vault "
                        "(impersonate=%s)",
                        bundle.get("impersonate") or "<unset>",
                    )
                except VaultNotFound:
                    log.info("mail/credentials: no Gmail SA in vault at startup")
                except Exception as exc:  # noqa: BLE001
                    log.warning(
                        "mail/credentials: vault SA read failed at startup: %s",
                        exc,
                    )
            # Also prime the stored inbox.source flag so the sync mailbox
            # factory can honor it with no DB round-trip.
            try:
                row = (
                    await s.execute(
                        _sel(TenantFeatureFlag).where(
                            TenantFeatureFlag.tenant_id == LJM_TENANT_ID,
                            TenantFeatureFlag.flag == "inbox.source",
                        )
                    )
                ).scalar_one_or_none()
                if row is not None:
                    v = row.value
                    if isinstance(v, dict):
                        set_vault_inbox_source_cache(str(v.get("value") or ""))
                    elif isinstance(v, str):
                        set_vault_inbox_source_cache(v)
            except Exception as exc:  # noqa: BLE001
                log.debug("mail/credentials: inbox.source prime skipped: %s", exc)
            # Prime the mail-read allowlist from SettingsRow (migration 0044).
            try:
                from app.identity.models import SettingsRow as _SR

                srow = (
                    await s.execute(_sel(_SR).where(_SR.id == 1))
                ).scalar_one_or_none()
                if srow is not None and srow.mail_read_allowlist:
                    set_vault_allowlist_cache(list(srow.mail_read_allowlist))
                    log.info(
                        "mail/credentials: primed mail_read_allowlist=%s",
                        list(srow.mail_read_allowlist),
                    )
            except Exception as exc:  # noqa: BLE001
                log.debug("mail/credentials: allowlist prime skipped: %s", exc)
    except Exception as exc:  # noqa: BLE001  pragma: no cover
        log.warning("mail/credentials: vault prime failed: %s", exc)


def resolve_sa_info(settings: Any) -> dict[str, Any] | None:
    """Env wins, else vault cache. Returns a parsed SA info dict or None."""
    env_sa = None
    try:
        env_sa = settings.gmail.sa_json.get_secret_value() if settings.gmail.sa_json else None
    except (AttributeError, ValueError):
        env_sa = None
    if env_sa:
        parsed = load_sa_info(env_sa)
        if parsed is not None:
            return parsed
    return _vault_sa_cache


def resolve_mailbox_source(settings: Any) -> str:
    """Resolve the effective mailbox source — env > stored flag > SA presence.

    Env override still wins: ``MAILBOX_SOURCE=gmail`` always returns ``gmail``.
    Beyond that, a stored ``inbox.source`` TenantFeatureFlag (set by the Settings
    → Connect Gmail flow and primed into the process cache at startup / refreshed
    on connect) wins over the env default ``simulated``. Finally, the presence
    of a vault-stored service account also counts as "owner connected via
    Settings" so a connect becomes live without a restart.
    """
    try:
        env_val = (getattr(settings, "mailbox_source", "") or "").strip().lower()
    except (AttributeError, ValueError):
        env_val = ""
    if env_val == "gmail":
        return "gmail"
    if _vault_inbox_source_cache == "gmail":
        return "gmail"
    if _vault_inbox_source_cache == "simulated":
        return "simulated"
    # No stored flag — the vault SA presence counts as owner-connected.
    if _vault_sa_cache is not None:
        return "gmail"
    return env_val or "simulated"


def resolve_mail_read_allowlist(settings: Any) -> set[str] | None:
    """Env > stored allowlist > ``None`` (no restriction).

    Env ``MAIL_READ_ALLOWLIST`` is a case-insensitive comma-separated list; a
    stored ``SettingsRow.mail_read_allowlist`` (primed into the vault cache)
    is a JSONB list of mailbox emails. Returns a lowercased set so callers
    can do a cheap `email.lower() in allowlist` check, or ``None`` when the
    gate is disabled.
    """
    env_val = ""
    try:
        env_val = (getattr(settings, "mail_read_allowlist", "") or "").strip()
    except (AttributeError, ValueError):
        env_val = ""
    if env_val:
        parts = [p.strip().lower() for p in env_val.split(",") if p.strip()]
        return set(parts) if parts else None
    cached = _vault_allowlist_cache
    if cached:
        return {str(x).strip().lower() for x in cached if str(x).strip()}
    return None


def resolve_impersonate(settings: Any) -> str:
    """Env wins, else vault cache. Returns the admin-impersonate email."""
    try:
        env_imp = (getattr(settings.gmail, "admin_impersonate", "") or "").strip()
    except (AttributeError, ValueError):
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
