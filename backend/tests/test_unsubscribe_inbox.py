"""Unsubscribe flow for inbox-originated sends.

Before this fix, ``/inbox/compose`` and ``/inbox/threads/{id}/reply`` signed
the unsubscribe token with a hashed pseudo-id (``abs(hash(email)) & 0x7FFFFFFF``)
that ``/unsubscribe`` could never resolve to a ``lead_contacts`` row — the
click returned 404 and no suppression was recorded, violating CAN-SPAM.

The fix: look up the contact by lowercased email at send time; if found,
sign with its real id (classic path). If not, sign with an email-keyed
token so the click still records a ``suppression`` row keyed by email and
future sends are blocked by the existing suppression check.
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.db import Base
from app.main import create_app
from app.models import Lead, LeadContact, Suppression


@pytest.fixture
async def engine():
    e = create_async_engine("sqlite+aiosqlite:///:memory:", future=True)
    async with e.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield e
    await e.dispose()


@pytest.fixture
async def sm(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


def _settings():
    from app.config import Settings

    return Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite:///:memory:",
        cron_secret=SecretStr("test-cron-secret"),
        unsubscribe_secret=SecretStr("test-unsub-secret"),
        unsubscribe_base_url="https://ljm.test",
        outreach_postal_address="123 Main St, Chicago IL",
    )


@pytest.fixture
async def app(sm):
    a = create_app()
    a.state.sessionmaker = sm
    a.state.settings = _settings()
    return a


@pytest.fixture
async def client(app):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c


# ---------- token helpers ---------------------------------------------------


def test_email_token_roundtrips():
    from app.shared.tokens import (
        sign_unsubscribe_email_token,
        verify_any_unsubscribe_token,
        verify_unsubscribe_email_token,
    )

    tok = sign_unsubscribe_email_token("A@EXAMPLE.com", "test-unsub-secret")
    # Normalises on sign + verify.
    assert verify_unsubscribe_email_token(tok, "test-unsub-secret") == "a@example.com"
    kv = verify_any_unsubscribe_token(tok, "test-unsub-secret")
    assert kv == ("email", "a@example.com")


def test_email_token_wrong_secret_fails():
    from app.shared.tokens import sign_unsubscribe_email_token, verify_unsubscribe_email_token

    tok = sign_unsubscribe_email_token("a@b.com", "s1")
    assert verify_unsubscribe_email_token(tok, "s2") is None


def test_email_token_tampered_payload_fails():
    from app.shared.tokens import sign_unsubscribe_email_token, verify_unsubscribe_email_token

    tok = sign_unsubscribe_email_token("a@b.com", "s1")
    # Swap payload for a different email — sig no longer matches.
    import base64

    other = base64.urlsafe_b64encode(b"attacker@evil.com").decode().rstrip("=")
    tampered = f"e.{other}.{tok.rsplit('.', 1)[1]}"
    assert verify_unsubscribe_email_token(tampered, "s1") is None


def test_contact_token_still_works():
    from app.shared.tokens import sign_unsubscribe_token, verify_any_unsubscribe_token

    tok = sign_unsubscribe_token(42, "s1")
    assert verify_any_unsubscribe_token(tok, "s1") == ("contact", 42)


def test_contact_token_rejects_email_prefix():
    """A legacy caller that only uses the int-only verify path must not see
    an email token as a bogus contact id."""
    from app.shared.tokens import sign_unsubscribe_email_token, verify_unsubscribe_token

    tok = sign_unsubscribe_email_token("a@b.com", "s1")
    assert verify_unsubscribe_token(tok, "s1") is None


# ---------- end-to-end: click resolves + suppresses -------------------------


async def test_inbox_sent_email_without_contact_unsubscribes_by_email(sm, client):
    """Fresh recipient we never enriched. Mint an email-keyed token (what the
    inbox render path now mints) → POST /unsubscribe → 200 and a Suppression
    row keyed by email."""
    from app.shared.tokens import sign_unsubscribe_email_token

    token = sign_unsubscribe_email_token("fresh@shipper.co", "test-unsub-secret")
    r = await client.post(f"/unsubscribe?t={token}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["email"] == "fresh@shipper.co"

    async with sm() as s:
        rows = (await s.execute(select(Suppression))).scalars().all()
    assert [r.email for r in rows] == ["fresh@shipper.co"]
    # The second click is idempotent — `already=True`.
    r2 = await client.post(f"/unsubscribe?t={token}")
    assert r2.status_code == 200
    assert r2.json()["already"] is True


async def test_inbox_sent_email_with_matching_contact_uses_contact_token(sm):
    """When a lead_contacts row exists for the recipient, the inbox render
    path mints the classic contact-id token — same path as outreach."""
    from app.identity.models import SettingsRow
    from app.inbox.service import _render_and_wrap  # type: ignore[attr-defined]
    from app.shared.tokens import verify_any_unsubscribe_token

    async with sm() as s:
        # Seed a per-tenant settings row with the shared secret.
        s.add(SettingsRow(id=1, unsubscribe_secret="test-unsub-secret"))
        s.add(Lead(id="MC-INB", name="Shipper", kind="Shipper", state="IL",
                   raw={}, evidence={}, recommendations=[]))
        await s.flush()
        c = LeadContact(lead_id="MC-INB", email="dispatch@shipper.co",
                        pipeline_status="new", is_decision_maker=True)
        s.add(c)
        await s.commit()
        await s.refresh(c)
        contact_id = c.id

    settings = _settings()

    async with sm() as s:
        _text, _html, headers = await _render_and_wrap(
            session=s,
            settings=settings,
            body_text="hello",
            subject="hi",
            design=None,
            to="Dispatch@Shipper.CO",  # case-insensitive match
        )

    assert "List-Unsubscribe" in headers
    url = headers["List-Unsubscribe"].strip("<>")
    assert "/unsubscribe?t=" in url
    token = url.split("t=", 1)[1]
    kv = verify_any_unsubscribe_token(token, "test-unsub-secret")
    assert kv == ("contact", contact_id)


async def test_inbox_sent_email_without_contact_mints_email_token(sm):
    """No lead_contacts row → inbox render mints the email-keyed token."""
    from app.identity.models import SettingsRow
    from app.inbox.service import _render_and_wrap  # type: ignore[attr-defined]
    from app.shared.tokens import verify_any_unsubscribe_token

    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="test-unsub-secret"))
        await s.commit()

    settings = _settings()

    async with sm() as s:
        _, _, headers = await _render_and_wrap(
            session=s,
            settings=settings,
            body_text="hello",
            subject="hi",
            design=None,
            to="never-seen@carrier.io",
        )

    url = headers["List-Unsubscribe"].strip("<>")
    token = url.split("t=", 1)[1]
    kv = verify_any_unsubscribe_token(token, "test-unsub-secret")
    assert kv == ("email", "never-seen@carrier.io")


async def test_inbox_unsubscribe_click_full_roundtrip(sm, client):
    """End-to-end: mint the token the inbox render path would mint for a
    contact-less recipient, click it, verify suppression. This is the
    actual user-visible CAN-SPAM path."""
    from app.identity.models import SettingsRow
    from app.inbox.service import _render_and_wrap  # type: ignore[attr-defined]

    async with sm() as s:
        s.add(SettingsRow(id=1, unsubscribe_secret="test-unsub-secret"))
        await s.commit()

    settings = _settings()
    async with sm() as s:
        _, _, headers = await _render_and_wrap(
            session=s,
            settings=settings,
            body_text="hello",
            subject="hi",
            design=None,
            to="recipient@outside.io",
        )
    url = headers["List-Unsubscribe"].strip("<>")
    token = url.split("t=", 1)[1]

    r = await client.post(f"/unsubscribe?t={token}")
    assert r.status_code == 200, r.text
    assert r.json()["email"] == "recipient@outside.io"

    async with sm() as s:
        supp = (await s.execute(select(Suppression))).scalars().all()
    assert any(x.email == "recipient@outside.io" for x in supp)
