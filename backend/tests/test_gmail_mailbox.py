"""Real GmailMailbox adapter tests — google client fully mocked.

Covers:
  * `_parse_gmail_message` — headers, bodies (nested parts), base64url,
    history_id, address lists.
  * `backfill` — paged list → batched get, yields RawMessage(s) in order.
  * `incremental` — `history.list` fan-out, cursor advanced per yield.
  * `incremental` fallback — a 404 historyId-expired falls into a bounded
    re-backfill window instead of silently dropping mail.

No real Google traffic; every service call goes through fakes.
"""

from __future__ import annotations

import base64
from datetime import UTC, datetime
from typing import Any

import pytest

from app.integrations.adapters.email.mailbox import (
    GmailMailbox,
    _parse_gmail_message,
)


def _b64(s: str) -> str:
    return base64.urlsafe_b64encode(s.encode("utf-8")).decode("ascii").rstrip("=")


def _make_msg(mid: str, hid: str, from_addr: str, body: str, subject: str = "hi") -> dict:
    ts = int(datetime(2026, 10, 1, 12, 0, tzinfo=UTC).timestamp() * 1000)
    return {
        "id": mid,
        "threadId": f"T-{mid}",
        "historyId": hid,
        "internalDate": str(ts),
        "labelIds": ["INBOX"],
        "snippet": body[:50],
        "payload": {
            "mimeType": "multipart/alternative",
            "headers": [
                {"name": "From", "value": f"Sender <{from_addr}>"},
                {"name": "To", "value": "ops@ljminternational.com, cc@ljminternational.com"},
                {"name": "Cc", "value": ""},
                {"name": "Subject", "value": subject},
                {"name": "Date", "value": "Thu, 01 Oct 2026 12:00:00 +0000"},
                {"name": "References", "value": "<a@x> <b@y>"},
                {"name": "In-Reply-To", "value": "<a@x>"},
            ],
            "parts": [
                {"mimeType": "text/plain", "body": {"data": _b64(body)}},
                {"mimeType": "text/html", "body": {"data": _b64(f"<p>{body}</p>")}},
            ],
        },
    }


def test_parse_gmail_message_full() -> None:
    raw = _make_msg("M1", "500", "broker@example.com", "need a truck LAX→DAL $2200")
    msg = _parse_gmail_message(raw, "ops@ljminternational.com")
    assert msg is not None
    assert msg.message_id == "M1"
    assert msg.thread_id == "T-M1"
    assert msg.history_id == "500"
    assert msg.from_addr == "broker@example.com"
    assert msg.to_addrs == ["ops@ljminternational.com", "cc@ljminternational.com"]
    assert msg.subject == "hi"
    assert "need a truck" in msg.body_text
    assert "<p>" in msg.body_html
    assert msg.references == ["<a@x>", "<b@y>"]
    assert msg.in_reply_to == "<a@x>"
    assert msg.labels == ["INBOX"]


def test_parse_returns_none_on_missing_ids() -> None:
    assert _parse_gmail_message({"payload": {}}, "x@y") is None


class _FakeRequest:
    def __init__(self, result: Any) -> None:
        self._result = result

    def execute(self) -> Any:
        if isinstance(self._result, Exception):
            raise self._result
        return self._result


class _FakeMessages:
    def __init__(self, pages: list[dict], store: dict[str, dict]) -> None:
        self._pages = pages
        self._store = store

    def list(self, **kwargs: Any) -> _FakeRequest:
        token = kwargs.get("pageToken")
        idx = 0 if token is None else int(token)
        if idx >= len(self._pages):
            return _FakeRequest({"messages": []})
        return _FakeRequest(self._pages[idx])

    def get(self, *, userId: str, id: str, format: str) -> _FakeRequest:
        if id in self._store:
            return _FakeRequest(self._store[id])
        return _FakeRequest(Exception(f"not found: {id}"))


class _FakeHistory:
    def __init__(self, pages: list[dict | Exception]) -> None:
        self._pages = pages

    def list(self, **kwargs: Any) -> _FakeRequest:
        token = kwargs.get("pageToken")
        idx = 0 if token is None else int(token)
        if idx >= len(self._pages):
            return _FakeRequest({"history": []})
        p = self._pages[idx]
        return _FakeRequest(p)


class _FakeUsers:
    def __init__(self, messages: _FakeMessages, history: _FakeHistory | None = None) -> None:
        self._messages = messages
        self._history = history

    def messages(self) -> _FakeMessages:
        return self._messages

    def history(self) -> _FakeHistory:
        assert self._history is not None
        return self._history


class _FakeService:
    def __init__(self, users: _FakeUsers) -> None:
        self._users = users

    def users(self) -> _FakeUsers:
        return self._users


def _make_mailbox(monkeypatch: pytest.MonkeyPatch, service: _FakeService) -> GmailMailbox:
    """Build a GmailMailbox with ``_service`` stubbed to our fake."""
    from app.config import GmailSettings, Settings

    gmail = GmailSettings()
    settings = Settings(
        database_url="sqlite+aiosqlite:///:memory:",
        gmail=gmail,  # type: ignore[arg-type]
    )
    mbox = GmailMailbox(settings)
    monkeypatch.setattr(mbox, "_service", lambda mailbox: service)
    return mbox


@pytest.mark.asyncio
async def test_backfill_pages_and_hydrates(monkeypatch: pytest.MonkeyPatch) -> None:
    store = {
        "M1": _make_msg("M1", "100", "a@brk.com", "first"),
        "M2": _make_msg("M2", "101", "b@brk.com", "second"),
        "M3": _make_msg("M3", "102", "c@brk.com", "third"),
    }
    pages = [
        {"messages": [{"id": "M1"}, {"id": "M2"}], "nextPageToken": "1"},
        {"messages": [{"id": "M3"}]},
    ]
    service = _FakeService(_FakeUsers(_FakeMessages(pages, store)))
    mbox = _make_mailbox(monkeypatch, service)

    collected = []
    async for m in mbox.backfill("ops@ljminternational.com", datetime(2026, 1, 1, tzinfo=UTC)):
        collected.append(m.message_id)
    assert collected == ["M1", "M2", "M3"]


@pytest.mark.asyncio
async def test_incremental_walks_history(monkeypatch: pytest.MonkeyPatch) -> None:
    store = {"X1": _make_msg("X1", "900", "x@brk.com", "hi")}
    history_pages = [
        {"history": [{"messagesAdded": [{"message": {"id": "X1"}}]}]},
    ]
    service = _FakeService(_FakeUsers(_FakeMessages([], store), _FakeHistory(history_pages)))
    mbox = _make_mailbox(monkeypatch, service)

    out: list[tuple[str, str]] = []
    async for msg, hid in mbox.incremental("ops@ljminternational.com", "500"):
        out.append((msg.message_id, hid))
    assert out == [("X1", "900")]


@pytest.mark.asyncio
async def test_incremental_falls_back_to_rebackfill_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _404(Exception):
        def __init__(self) -> None:
            super().__init__("Requested entity was not found. historyId.")

            class R:
                status = 404

            self.resp = R()

    store = {"R1": _make_msg("R1", "77", "r@brk.com", "recent")}
    pages = [{"messages": [{"id": "R1"}]}]
    service = _FakeService(
        _FakeUsers(_FakeMessages(pages, store), _FakeHistory([_404()])),
    )
    mbox = _make_mailbox(monkeypatch, service)
    out = []
    async for msg, hid in mbox.incremental("ops@ljminternational.com", "ancient"):
        out.append((msg.message_id, hid))
    # The 404 branch should have fed the backfill path which yields R1.
    assert out == [("R1", "77")]
