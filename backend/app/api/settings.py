"""Owner Settings — singleton row.

`GET /settings` upserts a default row on first read so the frontend is never blank.
`PUT /settings` patches any subset of fields.
"""

from __future__ import annotations

from datetime import datetime

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


class SettingsPatch(BaseModel):
    threshold: int | None = Field(default=None, ge=0, le=100)
    auto_send_enabled: bool | None = None
    auto_send_template_id: str | None = None
    tone: str | None = None
    daily_send_cap: int | None = Field(default=None, ge=1, le=1000)


def _serialize(row: SettingsRow) -> SettingsOut:
    return SettingsOut(
        threshold=row.threshold,
        auto_send_enabled=row.auto_send_enabled,
        auto_send_template_id=row.auto_send_template_id,
        tone=row.tone,
        daily_send_cap=row.daily_send_cap,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
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
        row.updated_at = datetime.utcnow()
        await s.commit()
        await s.refresh(row)
        return _serialize(row)
