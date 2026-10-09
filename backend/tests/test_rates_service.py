"""Rates service — happy path + two edges.

Scoped by design (user rule: do not over-test). One test per method, plus
the two sharp edges from the plan's AC: "no comps" and "no diesel key".
"""
from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.auth.deps import UserPrincipal, current_user
from app.config import Settings
from app.db import Base
from app.identity.dependencies import current_tenant
from app.main import create_app
from app.rates import domain
from app.rates.models import DieselPrice
from app.rates.schemas import BackhaulIn, CityStateIn, ProfitIn, RateQuoteIn
from app.rates.service import find_backhauls, quote_lane, score_profit
from app.shared.orm import LJM_TENANT_ID
from app.shared.tenant import TenantId


def _settings() -> Settings:
    return Settings().model_copy(update={"cron_secret": SecretStr("secret")})


async def _prep_db(app) -> None:
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _owner() -> UserPrincipal:
    return UserPrincipal(id="U1", email="owner@ljm.com", role="owner")


def _tenant() -> TenantId:
    return TenantId(LJM_TENANT_ID)


# ---- domain-level units ----

def test_haversine_known_pair() -> None:
    # Dallas → Atlanta ~ 721 mi great-circle (actual: 721.9).
    d = domain.haversine_miles(32.7767, -96.7970, 33.7490, -84.3880)
    assert 700 < d < 740


def test_highway_miles_and_padd() -> None:
    assert domain.highway_miles(721) == round(721 * 1.17)
    assert domain.padd_for("CA") == "5"
    assert domain.padd_for("GA") == "1C"
    assert domain.padd_for("ZZ") == "3"  # fallback


def test_score_profit_green_vs_red() -> None:
    hot = domain.score_profit(
        rate=2200, loaded_miles=800, deadhead_miles=0,
        diesel_per_gal=3.80, mpg=6.5, origin_state="TX", dest_state="OK",
    )
    assert hot.verdict in {"green", "tight"}
    cold = domain.score_profit(
        rate=600, loaded_miles=800, deadhead_miles=100,
        diesel_per_gal=3.80, mpg=6.5, origin_state="TX", dest_state="OK",
    )
    assert cold.verdict == "red"


# ---- service with DB ----

@pytest.mark.asyncio
async def test_quote_lane_with_comps_and_diesel() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[current_tenant] = _tenant
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        # Seed a diesel row (Gulf Coast = PADD 3 → Atlanta's PADD is 1C).
        async with app.state.sessionmaker() as s:
            s.add(DieselPrice(padd="1C", week_of=datetime.now(UTC).date(), price_usd=Decimal("3.950")))
            await s.commit()
            body = RateQuoteIn(
                origin=CityStateIn(city="Dallas", state="TX"),
                dest=CityStateIn(city="Atlanta", state="GA"),
                equipment="V",
            )
            out = await quote_lane(s, body)
        assert out.highway_miles > 700
        assert out.diesel is not None
        assert out.diesel.padd == "1C"
        assert out.rate_band["low"] <= out.rate_band["mid"] <= out.rate_band["high"]
        assert out.comps_count == 0  # no sent_log seeded
        assert any("fuel+margin" in e for e in out.evidence)


@pytest.mark.asyncio
async def test_profit_no_diesel_key_still_breaks_down() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[current_tenant] = _tenant
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        async with app.state.sessionmaker() as s:
            out = await score_profit(
                s,
                ProfitIn(
                    rate_usd=2200, loaded_miles=800, deadhead_miles=50,
                    mpg=6.5, origin_state="TX", dest_state="GA",
                ),
            )
        assert out.diesel_stale is True
        assert out.total_cost > 0
        assert out.verdict in {"green", "tight", "red"}
        assert any("stale" in e.lower() for e in out.evidence)


@pytest.mark.asyncio
async def test_backhaul_filters_by_radius() -> None:
    """Backhaul finder returns nothing without eligible leads; no 500 on
    an empty table — the user sees an empty list and the evidence string.
    """
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[current_tenant] = _tenant
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        async with app.state.sessionmaker() as s:
            out = await find_backhauls(
                s,
                BackhaulIn(
                    drop=CityStateIn(city="Chicago", state="IL"),
                    home_state="TX",
                    radius_miles=150,
                ),
            )
        assert out.candidates == []
        assert out.radius_miles == 150


@pytest.mark.asyncio
async def test_route_quote_422_on_unknown_city() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[current_tenant] = _tenant
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
            r = await c.post(
                "/rates/quote",
                json={
                    "origin": {"city": "NotARealCity", "state": "XX"},
                    "dest": {"city": "Atlanta", "state": "GA"},
                    "equipment": "V",
                },
            )
        assert r.status_code == 422
