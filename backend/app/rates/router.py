"""Rates — thin HTTP router.

Three POSTs: quote / profit / backhaul. Deps attached by `app/main.py`
alongside the other `user_only` routers.
"""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from app.rates import domain, service
from app.rates.schemas import (
    BackhaulIn,
    BackhaulOut,
    ProfitIn,
    ProfitOut,
    RateQuoteIn,
    RateQuoteOut,
)

router = APIRouter(prefix="/rates", tags=["rates"])


@router.post("/quote", response_model=RateQuoteOut)
async def rates_quote(body: RateQuoteIn, request: Request) -> RateQuoteOut:
    try:
        async with request.app.state.sessionmaker() as s:
            return await service.quote_lane(s, body)
    except domain.LaneResolveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/profit", response_model=ProfitOut)
async def rates_profit(body: ProfitIn, request: Request) -> ProfitOut:
    async with request.app.state.sessionmaker() as s:
        return await service.score_profit(s, body)


@router.post("/backhaul", response_model=BackhaulOut)
async def rates_backhaul(body: BackhaulIn, request: Request) -> BackhaulOut:
    try:
        async with request.app.state.sessionmaker() as s:
            return await service.find_backhauls(s, body)
    except domain.LaneResolveError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
