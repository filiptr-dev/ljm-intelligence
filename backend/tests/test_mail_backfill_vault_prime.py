"""The ``mail_backfill`` job must prime the vault SA cache itself.

Regression guard for the 2026-10-09 bug: procrastinate workers never run
FastAPI's lifespan, so ``_vault_sa_cache`` was ``None`` in the worker →
``get_mailbox_source`` dropped to ``SimulatedMailbox(messages={})`` →
every queued mailbox returned 0 reads with no Gmail call.
"""

from __future__ import annotations

import os

import pytest

from app.integrations.adapters.email.credentials import (
    ensure_vault_primed,
    reset_vault_cache,
    resolve_mailbox_source,
    vault_sa,
)


class _S:
    def __init__(self, mailbox_source: str = "simulated") -> None:
        self.mailbox_source = mailbox_source


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_vault_cache()
    yield
    reset_vault_cache()


@pytest.mark.asyncio
async def test_ensure_vault_primed_is_noop_when_already_primed() -> None:
    """Second call must not re-hit the DB — idempotency is the whole point."""
    from app.integrations.adapters.email.credentials import set_vault_cache

    set_vault_cache(
        '{"client_email":"x@y","private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}',
        "contact@ljminternational.com",
    )

    class _ExplodingSM:
        def __call__(self):
            raise AssertionError("sessionmaker must not be invoked when primed")

    await ensure_vault_primed(_ExplodingSM())
    assert vault_sa() is not None


@pytest.mark.asyncio
async def test_ensure_vault_primed_populates_cache_from_db() -> None:
    """pg16-only — proves the worker-side lazy prime reads the stored SA."""
    if os.environ.get("TEST_HARNESS", "").lower() != "pg16":
        pytest.skip("pg16 harness only — needs real Postgres + RLS")

    from sqlalchemy import text as _text

    from app.config import Settings
    from app.db import create_engine, create_sessionmaker
    from app.identity.credentials import CredentialVault, reset_vault_key_cache
    from app.identity.models import TenantFeatureFlag
    from app.shared.orm import LJM_TENANT_ID
    from app.shared.tenant import TenantId

    reset_vault_cache()
    reset_vault_key_cache()
    settings = Settings()
    engine = create_engine(settings)
    sm = create_sessionmaker(engine)

    sa_json = (
        '{"client_email":"svc@proj.iam.gserviceaccount.com",'
        '"private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}'
    )
    async with sm() as s:
        await s.execute(
            _text("SELECT set_config('app.tenant_id', :tid, true)"),
            {"tid": LJM_TENANT_ID},
        )
        vault = await CredentialVault.for_session(s)
        await vault.put(
            s, TenantId(LJM_TENANT_ID), "gmail", "service_account",
            {"sa_json": sa_json, "impersonate": "viktor@ljminternational.com"},
        )
        s.add(
            TenantFeatureFlag(
                id="vault_prime_lazy__",
                tenant_id=LJM_TENANT_ID,
                flag="inbox.source",
                value={"value": "gmail"},
            )
        )
        await s.commit()

    # Fresh "worker" process state — cache is empty, mimics procrastinate boot.
    reset_vault_cache()
    assert vault_sa() is None
    assert resolve_mailbox_source(_S()) == "simulated"

    # The job's first thing to run.
    await ensure_vault_primed(sm)

    assert vault_sa() is not None, "worker job must prime SA from vault"
    # And now the mailbox factory must resolve to Gmail — not SimulatedMailbox().
    assert resolve_mailbox_source(_S()) == "gmail"

    await engine.dispose()
