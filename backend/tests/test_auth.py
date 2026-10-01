"""Auth v1 — login, bearer-required protection, unsub stays public.

Hermetic: in-memory sqlite + hand-wired sessionmaker + per-test ``create_app``
with the conftest-installed auth bypass cleared on the app we own so the
real deps run.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.auth.passwords import hash_password, verify_password
from app.db import Base
from app.main import create_app
from app.models import SettingsRow, User

OWNER_EMAIL = "owner@ljm-demo.local"
OWNER_PASSWORD = "password"
JWT_SECRET = "test-jwt-secret-for-pytest-only-32chars"


def _settings():
    """Hermetic Settings — no .env, no real DB URL."""
    from app.config import Settings

    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        auth_jwt_secret_override=SecretStr(JWT_SECRET),
    )


@pytest.fixture
async def engine_sm():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    sm = async_sessionmaker(engine, expire_on_commit=False)
    # Seed the one owner + a settings row.
    async with sm() as s:
        s.add(SettingsRow(id=1, auth_jwt_secret=JWT_SECRET))
        s.add(
            User(
                id="01OWNERLJMDEMOACCOUNTSEED1",
                email=OWNER_EMAIL,
                name="LJM Owner",
                role="owner",
                password_hash=hash_password(OWNER_PASSWORD),
                is_active=True,
            )
        )
        s.add(
            User(
                id="01INACTIVE000000000000USER",
                email="disabled@ljm-demo.local",
                name="Disabled",
                role="staff",
                password_hash=hash_password(OWNER_PASSWORD),
                is_active=False,
            )
        )
        s.add(
            User(
                id="01GOOGLESSONOLOCALPASSWORD",
                email="sso@ljm-demo.local",
                name="SSO Only",
                role="staff",
                password_hash=None,
                is_active=True,
            )
        )
        await s.commit()
    yield engine, sm
    await engine.dispose()


@pytest.fixture
async def client(engine_sm, disable_auth_bypass) -> AsyncClient:
    _, sm = engine_sm
    app = create_app(_settings())
    disable_auth_bypass(app)  # real auth deps for THIS app
    app.state.sessionmaker = sm  # hand-wire DB; skip lifespan
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# --- hashing roundtrip ------------------------------------------------------


def test_hash_then_verify_roundtrip() -> None:
    h = hash_password("hunter2")
    assert verify_password("hunter2", h) is True
    assert verify_password("wrong", h) is False


# --- login happy path + wrong password --------------------------------------


@pytest.mark.asyncio
async def test_login_ok_returns_bearer(client: AsyncClient) -> None:
    r = await client.post("/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["token_type"] == "bearer"
    assert body["user"]["email"] == OWNER_EMAIL
    assert body["user"]["role"] == "owner"
    assert body["expires_in"] > 0
    assert isinstance(body["access_token"], str) and len(body["access_token"]) > 20


@pytest.mark.asyncio
async def test_login_bad_password_401(client: AsyncClient) -> None:
    r = await client.post("/auth/login", json={"email": OWNER_EMAIL, "password": "nope"})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid credentials"


@pytest.mark.asyncio
async def test_login_unknown_email_401(client: AsyncClient) -> None:
    r = await client.post("/auth/login", json={"email": "ghost@ljm-demo.local", "password": "password"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_login_inactive_user_401(client: AsyncClient) -> None:
    # A deactivated account must not log in, even with the right password.
    r = await client.post("/auth/login", json={"email": "disabled@ljm-demo.local", "password": OWNER_PASSWORD})
    assert r.status_code == 401
    assert r.json()["detail"] == "invalid credentials"


@pytest.mark.asyncio
async def test_login_sso_user_without_password_hash_401(client: AsyncClient) -> None:
    # A row with no local ``password_hash`` (future Google-SSO user) cannot
    # log in via the password route — must get the same generic 401.
    r = await client.post("/auth/login", json={"email": "sso@ljm-demo.local", "password": "anything"})
    assert r.status_code == 401


# --- /auth/me requires a bearer ---------------------------------------------


@pytest.mark.asyncio
async def test_me_401_without_token(client: AsyncClient) -> None:
    r = await client.get("/auth/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_me_200_with_token(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    token = login.json()["access_token"]
    r = await client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["email"] == OWNER_EMAIL


# --- protected route rejects anonymous --------------------------------------


@pytest.mark.asyncio
async def test_leads_401_without_token(client: AsyncClient) -> None:
    r = await client.get("/leads")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_leads_200_with_token(client: AsyncClient) -> None:
    login = await client.post("/auth/login", json={"email": OWNER_EMAIL, "password": OWNER_PASSWORD})
    token = login.json()["access_token"]
    r = await client.get("/leads", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert "items" in r.json()


# --- unsubscribe stays public -----------------------------------------------


@pytest.mark.asyncio
async def test_unsubscribe_get_stays_public(client: AsyncClient) -> None:
    # The GET renders an HTML page even for an invalid/missing token — the
    # route is reachable without a bearer. Confirming the status code is not
    # 401 is enough; the exact HTML is covered elsewhere.
    r = await client.get("/unsubscribe?token=garbage")
    assert r.status_code != 401


# --- /health stays public ---------------------------------------------------


@pytest.mark.asyncio
async def test_health_stays_public(client: AsyncClient) -> None:
    # Note: /health does its own DB ping via the engine on app.state. We only
    # hand-wired the sessionmaker, so this will likely report db:down — but
    # the route must respond 200 (never 500) and must not require a bearer.
    r = await client.get("/health")
    assert r.status_code == 200
    assert r.json()["ok"] in (True, False)
