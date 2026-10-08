"""ai_used / ai_error flags on ai_draft_reply + ai_rewrite (mirrors ai_draft_compose)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.inbox import service as svc


class _Provider:
    kind = "fake"

    def __init__(self, text: str | None = None, exc: Exception | None = None) -> None:
        self._text, self._exc = text, exc

    async def generate_text(self, prompt: str):
        if self._exc:
            raise self._exc
        return SimpleNamespace(status="ok", text=self._text)


@pytest.mark.asyncio
async def test_rewrite_flags() -> None:
    ok = await svc.ai_rewrite(body_text="hi", tone="direct", provider=_Provider("rewritten"))
    assert ok.ai_used is True and ok.ai_error is None and ok.body_text == "rewritten"
    bad = await svc.ai_rewrite(body_text="hi", tone="direct", provider=_Provider(exc=RuntimeError("x")))
    assert bad.ai_used is False and bad.ai_error == "error:RuntimeError" and bad.body_text == "hi"


@pytest.mark.asyncio
async def test_draft_reply_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    msg = SimpleNamespace(
        direction="in", broker_name="Bob", from_addr="bob@x.com", subject="Load",
        intent="routine", rate_usd=None, lane_from="A", lane_to="B", body_text="hello",
    )

    async def _thread(session, thread_id):
        return [msg]

    monkeypatch.setattr(svc, "get_thread", _thread)
    ok = await svc.ai_draft_reply(None, "t", provider=_Provider("AI reply"))
    assert ok.ai_used is True and ok.ai_error is None and ok.body_text == "AI reply"
    bad = await svc.ai_draft_reply(None, "t", provider=_Provider(exc=RuntimeError("x")))
    assert bad.ai_used is False and bad.ai_error == "error:RuntimeError"
