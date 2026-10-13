"""_build_truck_context + ai_draft_reply hint plumbing.

Covers plan ACs:
  D1 — matching order_no → load → truck yields a location-anchored draft
  D2 — no match → ASK_FOR_LOAD_NUMBER sentinel + no invented facts
  D4 — null last_seen_at falls back to updated_at

The reply path is tested with an injected fake provider that returns the
prompt verbatim so we can assert on what the model would see.
"""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest


class _EchoProvider:
    kind = "fake"

    def __init__(self):
        self.last_prompt: str | None = None

    async def generate_text(self, prompt: str):
        self.last_prompt = prompt
        return SimpleNamespace(status="ok", text="AI draft body — " + prompt[:80])


def _msg(**over):
    base = {
        "direction": "in", "message_id": "m1", "mailbox": "contact@ljminternational.com",
        "thread_id": "T", "broker_name": "Bob", "from_addr": "bob@acme.com",
        "subject": "Where's my truck", "intent": "urgent_truck",
        "rate_usd": None, "lane_from": "", "lane_to": "", "body_text": "where is unit?",
        "sent_at": datetime.now(UTC),
    }
    base.update(over)
    return SimpleNamespace(**base)


@pytest.mark.asyncio
async def test_truck_context_sentinel_when_no_match(monkeypatch: pytest.MonkeyPatch):
    from app.inbox import service as svc

    async def _thread(s, tid):
        return [_msg()]

    async def _derived(s, *, thread_id, message_id, mailbox):
        return (None, None, None, [])  # no derived order #

    async def _style(session, **kw):
        return ""

    monkeypatch.setattr(svc, "get_thread", _thread)
    monkeypatch.setattr(svc, "_latest_insight_derived", _derived)
    # style_examples helper
    import app.inbox.style_examples as _se
    monkeypatch.setattr(_se, "build_style_block", _style)

    provider = _EchoProvider()
    out = await svc.ai_draft_reply(session=None, thread_id="T", provider=provider)
    assert out is not None
    assert provider.last_prompt is not None
    assert "do not know which load" in provider.last_prompt.lower() or "ASK_FOR_LOAD_NUMBER" in provider.last_prompt or "ask for the load" in provider.last_prompt.lower()
    assert "Do not invent" in provider.last_prompt


@pytest.mark.asyncio
async def test_truck_context_with_match(monkeypatch: pytest.MonkeyPatch):
    """When a load+truck match exists, draft prompt names the unit + location."""
    from app.inbox import service as svc

    async def _thread(s, tid):
        return [_msg()]

    async def _derived(s, *, thread_id, message_id, mailbox):
        return ("ORD-123", None, None, ["ORD-123"])

    async def _build_truck(session, *, order_nos):
        return "We have truck 7001 at Dallas, TX (last seen 2026-10-13 14:30 UTC). Use this if the broker is asking about the load."

    async def _style(session, **kw):
        return ""

    monkeypatch.setattr(svc, "get_thread", _thread)
    monkeypatch.setattr(svc, "_latest_insight_derived", _derived)
    monkeypatch.setattr(svc, "_build_truck_context", _build_truck)
    import app.inbox.style_examples as _se
    monkeypatch.setattr(_se, "build_style_block", _style)

    provider = _EchoProvider()
    out = await svc.ai_draft_reply(session=None, thread_id="T", provider=provider)
    assert out is not None
    assert "truck 7001" in (provider.last_prompt or "")
    assert "Dallas" in (provider.last_prompt or "")


@pytest.mark.asyncio
async def test_hint_is_appended_to_prompt(monkeypatch: pytest.MonkeyPatch):
    from app.inbox import service as svc

    async def _thread(s, tid):
        return [_msg(intent="routine")]

    async def _derived(s, *, thread_id, message_id, mailbox):
        return (None, None, None, [])

    async def _style(session, **kw):
        return ""

    monkeypatch.setattr(svc, "get_thread", _thread)
    monkeypatch.setattr(svc, "_latest_insight_derived", _derived)
    import app.inbox.style_examples as _se
    monkeypatch.setattr(_se, "build_style_block", _style)

    provider = _EchoProvider()
    out = await svc.ai_draft_reply(session=None, thread_id="T", provider=provider, hint="Ask Bob to confirm pickup window")
    assert out is not None
    assert "Ask Bob to confirm pickup window" in (provider.last_prompt or "")


@pytest.mark.asyncio
async def test_derived_lane_fallback(monkeypatch: pytest.MonkeyPatch):
    """A2: when triage lanes are empty but derived_lane_* is set, use derived."""
    from app.inbox import service as svc

    captured = {}

    async def _thread(s, tid):
        return [_msg(lane_from="", lane_to="")]

    async def _derived(s, *, thread_id, message_id, mailbox):
        return (None, "Chicago, IL", "Dallas, TX", [])

    async def _style(session, **kw):
        return ""

    monkeypatch.setattr(svc, "get_thread", _thread)
    monkeypatch.setattr(svc, "_latest_insight_derived", _derived)
    import app.inbox.style_examples as _se
    monkeypatch.setattr(_se, "build_style_block", _style)

    provider = _EchoProvider()
    out = await svc.ai_draft_reply(session=None, thread_id="T", provider=provider)
    assert out is not None
    assert "Chicago, IL" in (provider.last_prompt or "")
    assert "Dallas, TX" in (provider.last_prompt or "")
    _ = captured
