"""/loads routes — thin router over ``app.integrations.loads_service``.

Reads come under ``user_only``. The ``refresh-all`` cron endpoint accepts
``X-Cron-Secret``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Header, HTTPException, Query, Request
from pydantic import BaseModel

from app.api._auth import check_secret
from app.config import Settings
from app.integrations.loads_service import (
    UnknownSourceError,
    list_loads as svc_list_loads,
    list_sources as svc_list_sources,
    refresh_all as svc_refresh_all,
    refresh_source as svc_refresh_source,
    test_source as svc_test_source,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/loads", tags=["loads"])


class LoadOut(BaseModel):
    id: int
    source: str
    broker_name: str
    origin_city: str | None
    origin_state: str | None
    dest_city: str | None
    dest_state: str | None
    pickup_date: datetime | None
    equipment: str | None
    rate_usd: float | None
    miles: int | None
    posted_at: datetime | None


class LoadsListOut(BaseModel):
    items: list[LoadOut]


class SourceOut(BaseModel):
    kind: str
    enabled: bool
    last_verified_at: datetime | None = None
    reason: str | None = None


class SourcesOut(BaseModel):
    items: list[SourceOut]


class ConnectionTestOut(BaseModel):
    ok: bool
    latency_ms: int
    reason: str | None
    sample_count: int


class RefreshStatsOut(BaseModel):
    kind: str
    read: int
    inserted: int
    skipped: int
    status: Literal["ok", "disabled", "error"]
    error: str | None = None


class RefreshAllOut(BaseModel):
    items: list[RefreshStatsOut]


@router.get("", response_model=LoadsListOut)
async def list_loads(request: Request, limit: int = Query(default=50, ge=1, le=200)) -> LoadsListOut:
    async with request.app.state.sessionmaker() as s:
        rows = await svc_list_loads(s, limit=limit)
    return LoadsListOut(items=[LoadOut(**r.__dict__) for r in rows])


@router.get("/sources", response_model=SourcesOut)
async def list_sources(request: Request) -> SourcesOut:
    settings: Settings = request.app.state.settings
    rows = await svc_list_sources(request.app.state.sessionmaker, settings)
    return SourcesOut(items=[SourceOut(**r.__dict__) for r in rows])


@router.post("/sources/{kind}/test", response_model=ConnectionTestOut)
async def test_source(kind: str, request: Request) -> ConnectionTestOut:
    settings: Settings = request.app.state.settings
    try:
        result = await svc_test_source(request.app.state.sessionmaker, settings, kind)
    except UnknownSourceError as exc:
        raise HTTPException(404, f"unknown source: {kind}") from exc
    return ConnectionTestOut(**result.__dict__)


@router.post("/sources/{kind}/refresh", response_model=RefreshStatsOut)
async def refresh_source(kind: str, request: Request) -> RefreshStatsOut:
    settings: Settings = request.app.state.settings
    try:
        result = await svc_refresh_source(request.app.state.sessionmaker, settings, kind)
    except UnknownSourceError as exc:
        raise HTTPException(404, f"unknown source: {kind}") from exc
    return RefreshStatsOut(**result.__dict__)


@router.post("/sources/refresh-all", response_model=RefreshAllOut)
async def refresh_all(
    request: Request,
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> RefreshAllOut:
    settings: Settings = request.app.state.settings
    check_secret(settings, x_cron_secret)
    result = await svc_refresh_all(request.app.state.sessionmaker, settings)
    return RefreshAllOut(items=[RefreshStatsOut(**r.__dict__) for r in result.items])


__all__ = ["router"]
