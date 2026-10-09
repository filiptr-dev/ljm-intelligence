"""CredentialVault round-trip (on PG16 — needs the `tenant_credentials` table)."""
from __future__ import annotations

import base64
import os
import secrets

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

pytestmark = [
    pytest.mark.skipif(
        not os.environ.get("DATABASE_URL_TEST_PG"),
        reason="set DATABASE_URL_TEST_PG=postgresql+psycopg://... to run",
    ),
    pytest.mark.asyncio,
]

LJM = "01LJMORGLJM00000000000000A"


async def test_put_get_round_trip():
    from app.identity.credentials import CredentialVault
    from app.shared.tenant import TenantId

    key = secrets.token_bytes(32)
    os.environ["TENANT_CRED_KEY"] = base64.b64encode(key).decode()
    vault = CredentialVault()

    engine = create_async_engine(
        os.environ["DATABASE_URL_TEST_PG"], connect_args={"prepare_threshold": None}
    )
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            async with s.begin():
                await vault.put(
                    s, TenantId(LJM), "gmail", "oauth",
                    secret={"client_id": "cid", "client_secret": "sek"},
                    meta={"env": "test"},
                )
            async with s.begin():
                got = await vault.get(s, TenantId(LJM), "gmail", "oauth")
            assert got == {"client_id": "cid", "client_secret": "sek"}
    finally:
        await engine.dispose()
