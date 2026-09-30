"""Owner Settings — singleton row.

`GET /settings` upserts a default row on first read so the frontend is never blank.
`PUT /settings` patches any subset of fields.
"""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.models import SettingsRow

router = APIRouter(prefix="/settings", tags=["settings"])


class SettingsOut(BaseModel):
    threshold: int
    auto_send_enabled: bool
    auto_send_template_id: str | None
    tone: str
    daily_send_cap: int
    updated_at: str
    # Enrichment scope (2026-09-30) — auto-outreach + fit-weight overrides.
    auto_outreach_enabled: bool
    auto_outreach_template_id: str | None
    auto_outreach_daily_cap: int
    auto_outreach_window_start_h: int
    auto_outreach_window_end_h: int
    auto_outreach_status_filter: str
    fit_weights: dict | None


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
    fit_weights: dict | None = None


def _serialize(row: SettingsRow) -> SettingsOut:
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
        fit_weights=row.fit_weights,
    )


async def _get_or_create(session) -> SettingsRow:
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if not row:
        row = SettingsRow(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        return _serialize(await _get_or_create(s))


@router.put("", response_model=SettingsOut)
async def put_settings(request: Request, patch: SettingsPatch) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        row = await _get_or_create(s)
        data = patch.model_dump(exclude_unset=True)
        for k, v in data.items():
            setattr(row, k, v)
        # DB column is naive-UTC; keep tz-aware `now()` then strip for schema parity.
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await s.commit()
        await s.refresh(row)
        return _serialize(row)
