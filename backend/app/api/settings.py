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
