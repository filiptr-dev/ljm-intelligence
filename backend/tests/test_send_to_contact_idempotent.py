"""send_to_contact twice for the same contact sends exactly once."""

from __future__ import annotations

from types import SimpleNamespace

import pytest


class _FakeSession:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _FakeEngine:
    async def dispose(self):
        pass


@pytest.mark.asyncio
async def test_send_to_contact_twice_sends_once(monkeypatch):
    from app import db
    from app.outreach import email_service, jobs, repository

    sent: list[str] = []
    log: set[tuple[int, str]] = set()

    async def fake_draft(sm, settings, **kw):
        return SimpleNamespace(subject="Hi", body="b", body_html=None)

    async def fake_send(sm, settings, *, to, subject, contact_id, **kw):
        sent.append(to)
        log.add((contact_id, subject))
        return SimpleNamespace(mode="simulated", ok=True)

    async def fake_get(s, cid):
        return SimpleNamespace(email="a@b.co", lead_id="L1")

    async def fake_sent(s, cid, subject):
        return (cid, subject) in log

    monkeypatch.setattr(db, "create_engine", lambda s: _FakeEngine())
    monkeypatch.setattr(db, "create_sessionmaker", lambda e: _FakeSession)
    monkeypatch.setattr(email_service, "draft", fake_draft)
    monkeypatch.setattr(email_service, "send", fake_send)
    monkeypatch.setattr(repository, "get_contact", fake_get)
    monkeypatch.setattr(repository, "contact_already_sent", fake_sent)

    await jobs.send_to_contact.func(7)
    await jobs.send_to_contact.func(7)
    assert sent == ["a@b.co"]
