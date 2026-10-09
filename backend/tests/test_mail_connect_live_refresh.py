"""Focused tests for the Settings → Connect-Gmail live-refresh contract.

Three fixes under test:
  1. ``credentials.resolve_mailbox_source`` — env wins, else stored
     ``inbox.source`` flag / vault-SA presence beats the default simulated.
  2. ``credentials.prime_from_vault`` + the ``set_*`` setters refresh the
     in-process cache so ``get_mailbox_source`` + ``/mail/status`` flip to
     gmail without a restart.
  3. ``GmailMailbox.list_mailboxes`` falls back to ``[impersonate]`` when the
     Admin Directory listing 403s / returns [] / is unset — the contact-only
     DWD path we actually ship against.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.adapters.email.credentials import (
    prime_from_vault,
    reset_vault_cache,
    resolve_mailbox_source,
    set_vault_cache,
    set_vault_inbox_source_cache,
    vault_impersonate,
    vault_sa,
)
from app.integrations.adapters.email.mailbox import (
    GmailMailbox,
    SimulatedMailbox,
    get_mailbox_source,
)


class _S:
    """Minimal settings shim — only the attrs the resolver + factory touch."""

    def __init__(
        self,
        mailbox_source: str = "simulated",
        admin_impersonate: str = "",
    ) -> None:
        self.mailbox_source = mailbox_source

        class _G:
            scopes_admin = ("https://www.googleapis.com/auth/admin.directory.user.readonly",)
            scopes_read = ("https://www.googleapis.com/auth/gmail.readonly",)
            scopes_send = ("https://www.googleapis.com/auth/gmail.send",)
            per_mailbox_rps = 10
            global_rps = 50
            impersonate = ""
            sa_json = None

        self.gmail = _G()
        self.gmail.admin_impersonate = admin_impersonate


@pytest.fixture(autouse=True)
def _clear_cache():
    reset_vault_cache()
    yield
    reset_vault_cache()


# ---- resolver ------------------------------------------------------------


def test_resolve_env_default_simulated_without_cache() -> None:
    assert resolve_mailbox_source(_S()) == "simulated"


def test_resolve_env_gmail_wins_over_cache() -> None:
    set_vault_inbox_source_cache("simulated")
    assert resolve_mailbox_source(_S(mailbox_source="gmail")) == "gmail"


def test_resolve_stored_flag_overrides_env_default() -> None:
    set_vault_inbox_source_cache("gmail")
    assert resolve_mailbox_source(_S()) == "gmail"


def test_resolve_vault_sa_presence_counts_as_connected() -> None:
    # No flag written yet, but the owner pasted an SA → treat as gmail.
    set_vault_cache(
        '{"client_email":"x@y","private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}',
        "contact@ljminternational.com",
    )
    assert resolve_mailbox_source(_S()) == "gmail"


def test_resolve_stored_simulated_wins_over_sa_presence() -> None:
    set_vault_cache(
        '{"client_email":"x@y","private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}',
        "contact@ljminternational.com",
    )
    set_vault_inbox_source_cache("simulated")
    assert resolve_mailbox_source(_S()) == "simulated"


# ---- factory switches without restart ------------------------------------


def test_get_mailbox_source_flips_live_after_cache_refresh() -> None:
    s = _S()
    # Fresh process — no cache, env default simulated → SimulatedMailbox.
    assert isinstance(get_mailbox_source(s), SimulatedMailbox)
    # Owner connects via Settings → cache primed in-process.
    set_vault_cache(
        '{"client_email":"x@y","private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}',
        "contact@ljminternational.com",
    )
    set_vault_inbox_source_cache("gmail")
    # Same settings object — no restart — now resolves to Gmail.
    assert isinstance(get_mailbox_source(s), GmailMailbox)
    # Disconnect → simulated again.
    reset_vault_cache()
    set_vault_inbox_source_cache("simulated")
    assert isinstance(get_mailbox_source(s), SimulatedMailbox)


# ---- directory-listing fallback -----------------------------------------


def _prime_sa() -> None:
    set_vault_cache(
        '{"client_email":"x@y","private_key":"-----BEGIN PRIVATE KEY-----\\nAA\\n-----END PRIVATE KEY-----\\n"}',
        "contact@ljminternational.com",
    )


@pytest.mark.asyncio
async def test_list_mailboxes_falls_back_when_admin_impersonate_unset() -> None:
    _prime_sa()
    mbox = GmailMailbox(_S(mailbox_source="gmail", admin_impersonate=""))
    # No admin subject → skip Directory entirely → single-mailbox fallback.
    assert await mbox.list_mailboxes() == ["contact@ljminternational.com"]


@pytest.mark.asyncio
async def test_list_mailboxes_falls_back_when_directory_403s(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_sa()
    mbox = GmailMailbox(_S(mailbox_source="gmail", admin_impersonate="admin@ljm.com"))

    def _boom(*a: Any, **kw: Any):
        # Simulate googleapiclient HttpError 403 — contact@ is not a Workspace admin.
        raise RuntimeError("<HttpError 403: 'Not Authorized to access this resource/api'>")

    # Stub the delegated-credentials builder so we don't need google-auth.
    monkeypatch.setattr(
        "app.integrations.adapters.email.mailbox.build_delegated_credentials",
        lambda *a, **kw: object(),
    )
    # Stub anyio.to_thread.run_sync to invoke the callable and let it raise.
    import anyio

    async def _fake_run_sync(fn, *args):
        return fn(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)

    # Stub googleapiclient build to raise when the callable executes.
    class _FakeGac:
        @staticmethod
        def build(*a: Any, **kw: Any):
            _boom()

    monkeypatch.setitem(
        __import__("sys").modules,
        "googleapiclient.discovery",
        _FakeGac,
    )
    out = await mbox.list_mailboxes()
    assert out == ["contact@ljminternational.com"]


@pytest.mark.asyncio
async def test_list_mailboxes_falls_back_on_empty_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _prime_sa()
    mbox = GmailMailbox(_S(mailbox_source="gmail", admin_impersonate="admin@ljm.com"))
    monkeypatch.setattr(
        "app.integrations.adapters.email.mailbox.build_delegated_credentials",
        lambda *a, **kw: object(),
    )

    class _Req:
        def execute(self):
            return {"users": []}

    class _UsersSvc:
        def list(self, **_):
            return _Req()

        def list_next(self, *_a, **_kw):
            return None

    class _Svc:
        def users(self):
            return _UsersSvc()

    class _FakeGac:
        @staticmethod
        def build(*a: Any, **kw: Any):
            return _Svc()

    monkeypatch.setitem(
        __import__("sys").modules,
        "googleapiclient.discovery",
        _FakeGac,
    )
    import anyio

    async def _fake_run_sync(fn, *args):
        return fn(*args)

    monkeypatch.setattr(anyio.to_thread, "run_sync", _fake_run_sync)
    out = await mbox.list_mailboxes()
    assert out == ["contact@ljminternational.com"]


# ---- prime_from_vault round-trip under RLS (pg16-only) --------------------


@pytest.mark.asyncio
async def test_prime_from_vault_reads_stored_sa_under_rls() -> None:
    """Prod regression guard: a stored SA must be re-primed into the cache
    at startup even though the lifespan session has no user/tenant context.
    """
    import os

    if os.environ.get("TEST_HARNESS", "").lower() != "pg16":
        pytest.skip("pg16 harness only — RLS semantics")

    from sqlalchemy import text as _text

    from app.config import Settings
    from app.db import create_engine, create_sessionmaker
    from app.identity.credentials import (
        CredentialVault,
        reset_vault_key_cache,
    )
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
            {"sa_json": sa_json, "impersonate": "contact@ljminternational.com"},
        )
        s.add(
            TenantFeatureFlag(
                id="prime_rls_test__",
                tenant_id=LJM_TENANT_ID,
                flag="inbox.source",
                value={"value": "gmail"},
            )
        )
        await s.commit()

    reset_vault_cache()
    assert vault_sa() is None

    await prime_from_vault(sm)

    assert vault_sa() is not None, "SA must be primed from vault under RLS"
    assert vault_impersonate() == "contact@ljminternational.com"
    assert resolve_mailbox_source(_S()) == "gmail"

    await engine.dispose()


# ---- backfill days-override --------------------------------------------


@pytest.mark.asyncio
async def test_ingest_backfill_days_overrides_months() -> None:
    """``days=7`` must produce a 7-day since cutoff, overriding months=12."""
    from datetime import UTC, datetime as _dt, timedelta as _td

    from app.integrations.adapters.email.ingest import ingest_backfill
    from app.integrations.adapters.email.mailbox import SimulatedMailbox

    calls: list[_dt] = []

    class _Spy(SimulatedMailbox):
        async def backfill(self, mailbox: str, since: _dt):  # type: ignore[override]
            calls.append(since)
            if False:
                yield  # make it an async generator

    class _NullSession:
        async def commit(self):
            return None

        async def rollback(self):
            return None

        async def execute(self, *_a, **_kw):
            class _R:
                def scalar_one_or_none(self):
                    return None

            return _R()

        def add(self, _x):
            return None

    stats = await ingest_backfill(
        _NullSession(),  # type: ignore[arg-type]
        _Spy(messages={}),
        "contact@ljminternational.com",
        months=12,
        days=7,
    )
    assert stats.read == 0  # empty corpus, but call made
    assert len(calls) == 1
    delta = _dt.now(UTC) - calls[0]
    # 7-day window — allow a few seconds of slack for the test runtime.
    assert _td(days=6, hours=23, minutes=59) <= delta <= _td(days=7, minutes=1)
