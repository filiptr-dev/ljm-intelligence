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

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field, field_validator

from app.identity.platform_service import (
    SettingsSnapshot,
    UnsubMissingError,
    get_settings as svc_get_settings,
    put_settings as svc_put_settings,
    validate_ai_features,
)
from app.models import SettingsRow

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

from app.identity.credentials import CredentialVault, VaultConfigError
from app.identity.models import TenantFeatureFlag
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId
from sqlalchemy import select as _sa_select, delete as _sa_delete
import json, secrets


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
    exposing creds on an env var. Vault needs ``TENANT_CRED_KEY``.
    """
    try:
        info = json.loads(payload.sa_json)
    except ValueError:
        return GmailConnectorOut(ok=False, reason="invalid_json")
    if not isinstance(info, dict) or "client_email" not in info or "private_key" not in info:
        return GmailConnectorOut(ok=False, reason="missing_sa_fields")
    try:
        vault = CredentialVault()
    except VaultConfigError as exc:
        return GmailConnectorOut(ok=False, reason=f"vault_unavailable:{exc}")
    session = request.state.session if hasattr(request.state, "session") else None
    # Vault + flag writes share one session.
    async with request.app.state.sessionmaker() as s:
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
