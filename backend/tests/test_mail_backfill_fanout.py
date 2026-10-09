"""`/mail/backfill` — mailbox omitted → fan-out; `hours` window honoured.

Three behaviours this test pins:

* `mailbox=None, mailboxes=None` → one `items[]` entry per mailbox the source
  lists (Directory via GmailMailbox, or the simulated corpus's four staff
  mailboxes).
* `mailboxes=[...]` → fan-out across the explicit allow-list only, no
  Directory call.
* `hours=2` → `ingest_backfill` is called with the hour window and the
  `since` datetime it computes is strictly within the last 2 hours.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.auth.deps import UserPrincipal, current_user, require_user_or_cron
from app.config import Settings
from app.db import Base
from app.integrations.adapters.email.ingest import ingest_backfill
from app.integrations.adapters.email.mailbox import RawMessage, SimulatedMailbox
from app.main import create_app


def _settings() -> Settings:
    return Settings().model_copy(
        update={"cron_secret": SecretStr("secret"), "mail_sender": "simulated"}
    )


async def _prep_db(app) -> None:
    engine = app.state.engine
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


def _owner() -> UserPrincipal:
    return UserPrincipal(id="U1", email="owner@ljm.com", role="owner")


@pytest.mark.asyncio
async def test_backfill_omitted_mailbox_fans_out() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[require_user_or_cron] = _owner
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        transport = ASGITransport(app=app)
        headers = {"X-Cron-Secret": "secret"}
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/mail/backfill", headers=headers, json={"days": 7})
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            # Simulated corpus lists 4 staff mailboxes.
            mailboxes = {it["mailbox"] for it in items}
            assert len(items) == 4
            assert mailboxes == {
                "contact@ljminternational.com",
                "ops@ljminternational.com",
                "dispatch@ljminternational.com",
                "accounts@ljminternational.com",
            }


@pytest.mark.asyncio
async def test_backfill_explicit_mailboxes_list_skips_directory() -> None:
    app = create_app(_settings())
    app.dependency_overrides[current_user] = _owner
    app.dependency_overrides[require_user_or_cron] = _owner
    async with app.router.lifespan_context(app):
        await _prep_db(app)
        transport = ASGITransport(app=app)
        headers = {"X-Cron-Secret": "secret"}
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post(
                "/mail/backfill",
                headers=headers,
                json={
                    "mailboxes": [
                        "contact@ljminternational.com",
                        "ops@ljminternational.com",
                    ],
                    "hours": 2,
                },
            )
            assert r.status_code == 200, r.text
            items = r.json()["items"]
            assert [it["mailbox"] for it in items] == [
                "contact@ljminternational.com",
                "ops@ljminternational.com",
            ]


@pytest.mark.asyncio
async def test_ingest_backfill_hours_window_wins() -> None:
    """`hours` beats `days` beats `months` — directly on the ingest layer so
    the window math is pinned without the HTTP shim in the way."""
    from sqlalchemy.ext.asyncio import (
        AsyncSession,
        async_sessionmaker,
        create_async_engine,
    )

    from app.db import Base as _Base

    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(_Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    now = datetime.now(UTC)
    mbx = "owner@test"

    def _m(mid: str, when: datetime) -> RawMessage:
        return RawMessage(
            message_id=mid, thread_id=f"T-{mid}", history_id=mid, mailbox=mbx,
            from_addr=f"{mid}@test", to_addrs=[mbx], cc_addrs=[], subject="s",
            sent_at=when, received_at=when, in_reply_to=None, references=[],
            body_text="", body_html="", labels=["INBOX"], raw={"id": mid},
        )

    # Three messages: 10m ago, 90m ago, 5h ago.
    source = SimulatedMailbox(
        messages={
            mbx: [
                _m("fresh", now - timedelta(minutes=10)),
                _m("midway", now - timedelta(minutes=90)),
                _m("stale", now - timedelta(hours=5)),
            ]
        }
    )

    async with maker() as s:
        stats = await ingest_backfill(s, source, mbx, months=12, hours=2)
    # Only the first two are within the last 2h; "stale" is beyond.
    assert stats.read == 2
    assert stats.upserted == 2

    await engine.dispose()
