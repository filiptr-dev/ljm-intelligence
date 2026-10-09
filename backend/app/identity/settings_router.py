"""Owner Settings — singleton row (platform-level).

Thin router over ``app.identity.platform_service``. The service holds the
merge/validate logic and the arm-time guard; the router is HTTP plumbing.

The unsubscribe **secret** is deliberately not patchable through the public API
— the migration seeds a durable value; the API only exposes an advisory
`unsub_secret_set` boolean so the UI can show "set" / "not set" without ever
leaking the token. The unsubscribe **base URL** is not configurable here — it
is a fixed public URL in backend config (``Settings.unsubscribe_base_url``) and
the footer is appended to every outgoing email by the send code.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.identity.models import SettingsRow
from app.identity.platform_service import (
    SettingsSnapshot,
    UnsubMissingError,
    get_settings as svc_get_settings,
    put_settings as svc_put_settings,
    validate_ai_features,
)

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    threshold: int
    auto_send_enabled: bool
    auto_send_template_id: str | None
    tone: str
    daily_send_cap: int
    updated_at: str
    auto_outreach_enabled: bool
    auto_outreach_template_id: str | None
    auto_outreach_daily_cap: int
    auto_outreach_window_start_h: int
    auto_outreach_window_end_h: int
    auto_outreach_status_filter: str
    auto_outreach_min_fit: int
    fit_weights: dict | None
    unsub_secret_set: bool
    unsub_config_ready: bool
    ai_features: dict


class SettingsPatch(BaseModel):
    threshold: int | None = Field(default=None, ge=0, le=100)
    auto_send_enabled: bool | None = None
    auto_send_template_id: str | None = None
    tone: str | None = None
    daily_send_cap: int | None = Field(default=None, ge=1, le=1000)
    auto_outreach_enabled: bool | None = None
    auto_outreach_template_id: str | None = None
    auto_outreach_daily_cap: int | None = Field(default=None, ge=1, le=1000)
    auto_outreach_window_start_h: int | None = Field(default=None, ge=0, le=23)
    auto_outreach_window_end_h: int | None = Field(default=None, ge=0, le=23)
    auto_outreach_status_filter: str | None = None
    auto_outreach_min_fit: int | None = Field(default=None, ge=0, le=100)
    fit_weights: dict | None = None
    ai_features: dict | None = None

    @field_validator("ai_features")
    @classmethod
    def _validate_ai_features(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        return validate_ai_features(value)


def _project(snap: SettingsSnapshot) -> SettingsOut:
    row: SettingsRow = snap.row
    return SettingsOut(
        threshold=row.threshold,
        auto_send_enabled=row.auto_send_enabled,
        auto_send_template_id=row.auto_send_template_id,
        tone=row.tone,
        daily_send_cap=row.daily_send_cap,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        auto_outreach_enabled=row.auto_outreach_enabled,
        auto_outreach_template_id=row.auto_outreach_template_id,
        auto_outreach_daily_cap=row.auto_outreach_daily_cap,
        auto_outreach_window_start_h=row.auto_outreach_window_start_h,
        auto_outreach_window_end_h=row.auto_outreach_window_end_h,
        auto_outreach_status_filter=row.auto_outreach_status_filter,
        auto_outreach_min_fit=row.auto_outreach_min_fit,
        fit_weights=row.fit_weights,
        unsub_secret_set=snap.unsub_secret_set,
        unsub_config_ready=snap.unsub_config_ready,
        ai_features=snap.ai_features,
    )


@router.get("", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        snap = await svc_get_settings(s, request.app.state.settings)
    return _project(snap)


@router.put("", response_model=SettingsOut)
async def put_settings(request: Request, patch: SettingsPatch) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        try:
            snap = await svc_put_settings(
                s,
                request.app.state.settings,
                patch.model_dump(exclude_unset=True),
            )
        except UnsubMissingError as e:
            raise HTTPException(
                status_code=409,
                detail={
                    "error": "unsub_config_missing",
                    "missing": e.missing,
                    "message": "Unsubscribe link cannot be built; auto-outreach cannot be armed.",
                },
            ) from e
    return _project(snap)


# ---- Connector settings — Gmail owner-only (plan Step 7) ------------------

import json
import secrets

from sqlalchemy import delete as _sa_delete, select as _sa_select

from app.identity.credentials import CredentialVault, VaultConfigError
from app.identity.models import TenantFeatureFlag
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId


class GmailConnectorIn(BaseModel):
    sa_json: str = Field(min_length=10, max_length=100_000)
    impersonate: str = Field(default="", max_length=320)


class GmailConnectorOut(BaseModel):
    ok: bool
    fingerprint: str | None = None
    reason: str | None = None


class OwnerSwitchIn(BaseModel):
    enabled: bool
    confirm: str = Field(default="", description="Must equal 'CONFIRM' to flip ON")


class OwnerSwitchOut(BaseModel):
    ok: bool
    flag: str
    value: bool


async def _set_flag(session, flag: str, value: dict) -> None:
    existing = (
        await session.execute(
            _sa_select(TenantFeatureFlag).where(
                TenantFeatureFlag.tenant_id == LJM_TENANT_ID,
                TenantFeatureFlag.flag == flag,
            )
        )
    ).scalar_one_or_none()
    if existing:
        existing.value = value
    else:
        session.add(TenantFeatureFlag(
            id=secrets.token_hex(13),
            tenant_id=LJM_TENANT_ID,
            flag=flag,
            value=value,
        ))


async def _get_flag_bool(session, flag: str, default: bool = False) -> bool:
    row = (
        await session.execute(
            _sa_select(TenantFeatureFlag).where(
                TenantFeatureFlag.tenant_id == LJM_TENANT_ID,
                TenantFeatureFlag.flag == flag,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return default
    v = row.value
    if isinstance(v, dict):
        return bool(v.get("enabled", default))
    return bool(v)


@router.post("/connectors/gmail", response_model=GmailConnectorOut)
async def connect_gmail(payload: GmailConnectorIn, request: Request) -> GmailConnectorOut:
    """Encrypt + store Gmail service-account JSON in the vault.

    This is the owner-only path that lets LJM grant the connector without
    exposing creds on an env var. The vault key resolves env → DB → bootstrap,
    so no ``TENANT_CRED_KEY`` env is required on Render.
    """
    try:
        info = json.loads(payload.sa_json)
    except ValueError:
        return GmailConnectorOut(ok=False, reason="invalid_json")
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        return GmailConnectorOut(ok=False, reason="missing_sa_fields")
    # Vault + flag writes share one session.
    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            return GmailConnectorOut(ok=False, reason=f"vault_unavailable:{exc}")
        await vault.put(
            s, TenantId(LJM_TENANT_ID), "gmail", "service_account",
            {"sa_json": payload.sa_json, "impersonate": payload.impersonate},
        )
        await _set_flag(s, "inbox.source", {"value": "gmail"})
        await s.commit()
    import hashlib
    fp = hashlib.sha256(str(info.get("client_email", "")).encode()).hexdigest()[-12:]
    return GmailConnectorOut(ok=True, fingerprint=fp)


@router.delete("/connectors/gmail", response_model=GmailConnectorOut)
async def revoke_gmail(request: Request) -> GmailConnectorOut:
    """Delete the stored Gmail credential + flip inbox.source back to simulated."""
    async with request.app.state.sessionmaker() as s:
        from app.identity.models import TenantCredential
        await s.execute(
            _sa_delete(TenantCredential).where(
                TenantCredential.tenant_id == LJM_TENANT_ID,
                TenantCredential.connector == "gmail",
            )
        )
        await _set_flag(s, "inbox.source", {"value": "simulated"})
        await _set_flag(s, "inbox.send_via_gmail", {"enabled": False})
        await s.commit()
    return GmailConnectorOut(ok=True)


@router.post("/connectors/gmail/send-switch", response_model=OwnerSwitchOut)
async def send_switch(payload: OwnerSwitchIn, request: Request) -> OwnerSwitchOut:
    """Flip ``inbox.send_via_gmail`` ON/OFF. ON requires ``confirm='CONFIRM'``.

    Mirrors the superadmin-tenant-delete type-to-confirm pattern — this is
    the one action that can embarrass LJM in front of a broker, so it's
    always a two-step gesture.
    """
    if payload.enabled and payload.confirm != "CONFIRM":
        raise HTTPException(status_code=400, detail="confirm='CONFIRM' required to enable")
    async with request.app.state.sessionmaker() as s:
        await _set_flag(s, "inbox.send_via_gmail", {"enabled": payload.enabled})
        await s.commit()
    return OwnerSwitchOut(ok=True, flag="inbox.send_via_gmail", value=payload.enabled)


@router.post("/connectors/gmail/inbox-source", response_model=OwnerSwitchOut)
async def inbox_source_switch(payload: OwnerSwitchIn, request: Request) -> OwnerSwitchOut:
    """Flip ``inbox.source`` between simulated (False) and gmail (True)."""
    async with request.app.state.sessionmaker() as s:
        await _set_flag(s, "inbox.source", {"value": "gmail" if payload.enabled else "simulated"})
        await s.commit()
    return OwnerSwitchOut(ok=True, flag="inbox.source", value=payload.enabled)


# ---- Connector settings — Load-board logins (per source) -------------------
# Owner-only path for pasting load-board usernames + passwords straight into
# the DB vault. No env vars needed — the vault key resolves env → DB → bootstrap
# (see ``app.identity.credentials.effective_vault_key``). The password is write-
# only: the readback endpoint reports ``configured: true`` + a masked username,
# never the password itself.

LOADBOARD_SRCS = ("dat", "truckstop", "loadboard123", "chr")


class LoadboardCredIn(BaseModel):
    username: str = Field(min_length=1, max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class LoadboardCredOut(BaseModel):
    source: str
    configured: bool
    username_masked: str | None = None
    last_run_status: str | None = None
    last_run_at: str | None = None
    reason: str | None = None


class LoadboardCredsListOut(BaseModel):
    items: list[LoadboardCredOut]
    broker_page_urls: list[dict]


class BrokerPageUrlsIn(BaseModel):
    urls: list[dict] = Field(default_factory=list)


def _mask_username(u: str) -> str:
    u = (u or "").strip()
    if "@" in u:
        local, _, domain = u.partition("@")
        return (local[:2] + "***") + "@" + domain
    if len(u) <= 3:
        return "***"
    return u[:2] + "***" + u[-1:]


def _loadboard_connector(src: str) -> str:
    if src not in LOADBOARD_SRCS:
        raise HTTPException(404, f"unknown source: {src}")
    return f"loadboard_{src}"


async def _last_run(session, src: str) -> tuple[str | None, str | None]:
    """Return ``(status, iso_started_at)`` for the most recent agent_run."""
    from app.prospecting.models import AgentRun

    row = (
        await session.execute(
            _sa_select(AgentRun)
            .where(AgentRun.source == src)
            .order_by(AgentRun.started_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if row is None:
        return None, None
    return row.status, (row.started_at.isoformat() if row.started_at else None)


@router.get("/connectors/loadboard", response_model=LoadboardCredsListOut)
async def list_loadboard_creds(request: Request) -> LoadboardCredsListOut:
    """One row per source + the broker-page URL allowlist."""
    from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound

    items: list[LoadboardCredOut] = []
    async with request.app.state.sessionmaker() as s:
        # Vault key resolves env → DB → bootstrap; a VaultConfigError here would
        # mean we can't even write a key (e.g. no settings row and bootstrap
        # failed). In that case every slot is "not configured" with a reason.
        try:
            vault: CredentialVault | None = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            vault = None
            vault_reason = f"vault_unavailable:{exc}"
        else:
            vault_reason = None
        for src in LOADBOARD_SRCS:
            bundle: dict | None = None
            if vault is not None:
                try:
                    bundle = await vault.get(
                        s, TenantId(LJM_TENANT_ID), _loadboard_connector(src), "password"
                    )
                except VaultNotFound:
                    bundle = None
                except Exception:  # noqa: BLE001
                    bundle = None
            status, started = await _last_run(s, src)
            items.append(
                LoadboardCredOut(
                    source=src,
                    configured=bool(bundle),
                    username_masked=_mask_username(str((bundle or {}).get("username") or "")) if bundle else None,
                    last_run_status=status,
                    last_run_at=started,
                    reason=vault_reason if not bundle else None,
                )
            )
        # broker-page URLs live on the settings row (migration 0025).
        row = (
            await s.execute(_sa_select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        urls = list((getattr(row, "load_source_urls", None) or []) if row else [])
    return LoadboardCredsListOut(items=items, broker_page_urls=urls)


@router.post("/connectors/loadboard/{src}", response_model=LoadboardCredOut)
async def set_loadboard_cred(src: str, payload: LoadboardCredIn, request: Request) -> LoadboardCredOut:
    """Encrypt + store username+password for one load board.

    The vault key resolves env → DB → bootstrap: no ``TENANT_CRED_KEY`` env
    is needed on Render. The response never echoes the password back.
    """
    from app.identity.credentials import CredentialVault, VaultConfigError

    conn = _loadboard_connector(src)
    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            return LoadboardCredOut(source=src, configured=False, reason=f"vault_unavailable:{exc}")
        await vault.put(
            s, TenantId(LJM_TENANT_ID), conn, "password",
            {"username": payload.username, "password": payload.password},
        )
        await s.commit()
    return LoadboardCredOut(
        source=src,
        configured=True,
        username_masked=_mask_username(payload.username),
    )


@router.delete("/connectors/loadboard/{src}", response_model=LoadboardCredOut)
async def clear_loadboard_cred(src: str, request: Request) -> LoadboardCredOut:
    """Delete the stored credential for one load board."""
    from app.identity.models import TenantCredential

    conn = _loadboard_connector(src)
    async with request.app.state.sessionmaker() as s:
        await s.execute(
            _sa_delete(TenantCredential).where(
                TenantCredential.tenant_id == LJM_TENANT_ID,
                TenantCredential.connector == conn,
            )
        )
        await s.commit()
    return LoadboardCredOut(source=src, configured=False)


@router.post("/connectors/loadboard/{src}/test", response_model=LoadboardCredOut)
async def test_loadboard_cred(src: str, request: Request) -> LoadboardCredOut:
    """Shallow check: credential present + vault decrypts + last-run status.

    This never actually logs into DAT/Truckstop/etc — that happens inside
    the sidecar on the GH Actions runner. The purpose of this verb is "did
    the password save survive the round-trip through AES-GCM?" so the UI
    can show a green check immediately after a save.
    """
    from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound

    conn = _loadboard_connector(src)
    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            return LoadboardCredOut(source=src, configured=False, reason=f"vault_unavailable:{exc}")
        try:
            bundle = await vault.get(s, TenantId(LJM_TENANT_ID), conn, "password")
        except VaultNotFound:
            return LoadboardCredOut(source=src, configured=False, reason="no_credential")
        except Exception as exc:  # noqa: BLE001
            return LoadboardCredOut(source=src, configured=False, reason=f"decrypt_failed:{exc}")
        status, started = await _last_run(s, src)
    return LoadboardCredOut(
        source=src,
        configured=True,
        username_masked=_mask_username(str(bundle.get("username") or "")),
        last_run_status=status,
        last_run_at=started,
    )


@router.put("/connectors/loadboard/broker-page-urls", response_model=LoadboardCredsListOut)
async def set_broker_page_urls(payload: BrokerPageUrlsIn, request: Request) -> LoadboardCredsListOut:
    """Replace the public-broker-board URL allowlist (one entry per URL).

    Each entry is ``{"label": str, "url": str, "enabled": bool}``. Lives on
    the singleton settings row; the ``ai_page`` source reads it.
    """
    cleaned: list[dict] = []
    for raw in payload.urls or []:
        if not isinstance(raw, dict):
            continue
        url = str(raw.get("url") or "").strip()
        if not url or not (url.startswith("http://") or url.startswith("https://")):
            continue
        cleaned.append(
            {
                "label": str(raw.get("label") or "")[:120],
                "url": url[:2048],
                "enabled": bool(raw.get("enabled", True)),
            }
        )
    async with request.app.state.sessionmaker() as s:
        row = (
            await s.execute(_sa_select(SettingsRow).where(SettingsRow.id == 1))
        ).scalar_one_or_none()
        if row is None:
            row = SettingsRow(id=1)
            s.add(row)
        row.load_source_urls = cleaned
        await s.commit()
    # Re-use the GET projection so the client gets a consistent payload.
    return await list_loadboard_creds(request)


# ---- Driver dropdown + agent kill switch (migration 0025 + 0027) ----------
# Env wins when a ``LOADS_<SRC>_DRIVER`` or ``LOADS_AGENT_KILL`` is explicitly
# set on the process; otherwise the DB value stored here is used. The
# registry primes an in-process overlay from this row at app startup and we
# refresh that overlay on each write so the next ``/loads`` call reflects it.


_DRIVER_FIELDS = {
    "dat": "loads_dat_driver",
    "chr": "loads_chr_driver",
    "loadboard123": "loads_lb123_driver",
    "truckstop": "loads_truckstop_driver",
}


class LoadboardDriverIn(BaseModel):
    # One or more of the keys below; omitted keys keep their current value.
    dat: Literal["off", "api", "agent"] | None = None
    chr: Literal["off", "api", "agent"] | None = None
    loadboard123: Literal["off", "api", "agent"] | None = None
    truckstop: Literal["off", "api", "agent"] | None = None
    agent_kill: Literal["off", "on"] | None = None


class LoadboardDriverOut(BaseModel):
    dat: str
    chr: str
    loadboard123: str
    truckstop: str
    agent_kill: str
    # True if any ``LOADS_*_DRIVER`` env is set to something other than ``off``;
    # the UI uses this to warn that the DB value is being overridden by env.
    env_override_active: bool


def _env_overrides_active(settings) -> bool:
    for attr in _DRIVER_FIELDS.values():
        if str(getattr(settings, attr, "off") or "off") != "off":
            return True
    if (getattr(settings, "loads_agent_kill", "") or "") == "1":
        return True
    return False


async def _load_driver_row(session) -> SettingsRow:
    row = (
        await session.execute(_sa_select(SettingsRow).where(SettingsRow.id == 1))
    ).scalar_one_or_none()
    if row is None:
        row = SettingsRow(id=1)
        session.add(row)
        await session.flush()
    return row


def _project_drivers(row: SettingsRow, env_override: bool) -> LoadboardDriverOut:
    return LoadboardDriverOut(
        dat=getattr(row, "loads_dat_driver", "off") or "off",
        chr=getattr(row, "loads_chr_driver", "off") or "off",
        loadboard123=getattr(row, "loads_lb123_driver", "off") or "off",
        truckstop=getattr(row, "loads_truckstop_driver", "off") or "off",
        agent_kill=getattr(row, "loads_agent_kill", "off") or "off",
        env_override_active=env_override,
    )


# Note: this route lives *before* the ``/connectors/loadboard/{src}`` matcher
# further up because FastAPI resolves in registration order and ``drivers``
# must not be captured as a src slug. We re-register nothing here; the file
# order already placed the ``{src}`` routes above, so we use an explicit
# non-overlapping path (``/drivers``) that cannot be matched as a source
# name (the ``_loadboard_connector`` guard would 404 ``drivers`` anyway).


@router.get("/connectors/loadboard/drivers", response_model=LoadboardDriverOut)
async def get_loadboard_drivers(request: Request) -> LoadboardDriverOut:
    from app.config import Settings as _Settings

    settings: _Settings = request.app.state.settings
    async with request.app.state.sessionmaker() as s:
        row = await _load_driver_row(s)
        await s.commit()
    return _project_drivers(row, _env_overrides_active(settings))


@router.put("/connectors/loadboard/drivers", response_model=LoadboardDriverOut)
async def put_loadboard_drivers(payload: LoadboardDriverIn, request: Request) -> LoadboardDriverOut:
    """Persist driver + kill-switch values on the settings row.

    Only the fields the caller sends are updated. The registry overlay is
    refreshed in-process so the next ``/loads/sources`` call sees the change
    without a restart. Env still wins at read time — set ``LOADS_<SRC>_DRIVER``
    to anything other than ``off`` to pin a source regardless of this DB value.
    """
    from app.config import Settings as _Settings
    from app.integrations.adapters.loadboard.registry import set_db_overlay

    settings: _Settings = request.app.state.settings
    patch = payload.model_dump(exclude_unset=True)
    async with request.app.state.sessionmaker() as s:
        row = await _load_driver_row(s)
        if "dat" in patch:
            row.loads_dat_driver = patch["dat"]
        if "chr" in patch:
            row.loads_chr_driver = patch["chr"]
        if "loadboard123" in patch:
            row.loads_lb123_driver = patch["loadboard123"]
        if "truckstop" in patch:
            row.loads_truckstop_driver = patch["truckstop"]
        if "agent_kill" in patch:
            row.loads_agent_kill = patch["agent_kill"]
        await s.commit()
        await s.refresh(row)
    # Mirror into the in-process overlay the registry reads from.
    set_db_overlay(
        dat=row.loads_dat_driver,
        chr=row.loads_chr_driver,
        loadboard123=row.loads_lb123_driver,
        truckstop=row.loads_truckstop_driver,
        kill=row.loads_agent_kill,
    )
    return _project_drivers(row, _env_overrides_active(settings))


# ---- EIA diesel API key (plan 2026-10-09-tools-rates-profit-backhaul) ---
# Stored in the vault; absent key is a graceful "diesel not configured" in
# the rate tools — never a boot-time error. No new env var.


class EiaKeyIn(BaseModel):
    api_key: str = Field(min_length=4, max_length=200)


class EiaKeyOut(BaseModel):
    configured: bool
    key_masked: str | None = None
    reason: str | None = None


def _mask_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 6:
        return "•" * len(k)
    return f"{k[:3]}…{k[-3:]}"


@router.get("/connectors/eia", response_model=EiaKeyOut)
async def get_eia_key(request: Request) -> EiaKeyOut:
    """Status-only read: configured + masked key, never plaintext."""
    from app.identity.credentials import CredentialVault, VaultConfigError, VaultNotFound

    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            return EiaKeyOut(configured=False, reason=f"vault_unavailable:{exc}")
        try:
            bundle = await vault.get(s, TenantId(LJM_TENANT_ID), "eia", "api_key")
        except VaultNotFound:
            return EiaKeyOut(configured=False)
        except Exception as exc:  # noqa: BLE001
            return EiaKeyOut(configured=False, reason=f"decrypt_failed:{exc}")
    key = str((bundle or {}).get("api_key") or "")
    return EiaKeyOut(configured=bool(key), key_masked=_mask_key(key) if key else None)


@router.post("/connectors/eia", response_model=EiaKeyOut)
async def set_eia_key(payload: EiaKeyIn, request: Request) -> EiaKeyOut:
    from app.identity.credentials import CredentialVault, VaultConfigError

    async with request.app.state.sessionmaker() as s:
        try:
            vault = await CredentialVault.for_session(s)
        except VaultConfigError as exc:
            return EiaKeyOut(configured=False, reason=f"vault_unavailable:{exc}")
        await vault.put(
            s, TenantId(LJM_TENANT_ID), "eia", "api_key",
            {"api_key": payload.api_key},
        )
        await s.commit()
    return EiaKeyOut(configured=True, key_masked=_mask_key(payload.api_key))


@router.delete("/connectors/eia", response_model=EiaKeyOut)
async def clear_eia_key(request: Request) -> EiaKeyOut:
    from app.identity.models import TenantCredential

    async with request.app.state.sessionmaker() as s:
        await s.execute(
            _sa_delete(TenantCredential).where(
                TenantCredential.tenant_id == LJM_TENANT_ID,
                TenantCredential.connector == "eia",
                TenantCredential.kind == "api_key",
            )
        )
        await s.commit()
    return EiaKeyOut(configured=False)
