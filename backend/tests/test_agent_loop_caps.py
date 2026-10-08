"""Agent-browser Gemini tool-loop — caps + login-challenge detection.

Mocks Playwright + the LLM so no real network traffic happens. The point
of this test is strictly the loop's safety rails:

* ``max_steps`` → status ``cap_exceeded``, zero loads.
* page text containing "sign in" → status ``login_challenge`` before any
  LLM call.
* allowlist violation → status ``error`` with ``url_not_in_allowlist``.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from unittest.mock import patch

import httpx
import pytest

from app.integrations.adapters.loadboard.agent_browser.agent import (
    AgentCaps,
    run,
)

pytestmark = pytest.mark.asyncio


@dataclass
class _Call:
    text: str
    input_tokens: int = 10
    output_tokens: int = 5
    status: str = "ok"
    error: str | None = None


class _StubProvider:
    def __init__(self, scripted_texts: list[str]) -> None:
        self._texts = list(scripted_texts)

    async def generate_json(self, prompt: str) -> _Call:
        if not self._texts:
            return _Call(text='{"tool":"finish","args":{"payload":{"loads":[]}}}')
        return _Call(text=self._texts.pop(0))


def _mock_transport_handler(script: dict[str, dict]):
    """Return a Mock transport that answers the sidecar's HTTP tool calls.

    For ``finish`` specifically, the request body carries the ``payload``;
    we echo it back so the agent sees ``{"ok": True, "data": {"payload": ...}}``
    just like the real sidecar does.
    """

    def _handler(request: httpx.Request) -> httpx.Response:
        tool = request.url.path.rstrip("/").split("/")[-1]
        if tool == "finish":
            body = json.loads(request.content.decode() or "{}")
            return httpx.Response(200, json={"ok": True, "data": {"payload": body.get("payload", {})}})
        return httpx.Response(200, json={"ok": True, "data": script.get(tool, {})})

    return httpx.MockTransport(_handler)


async def test_run_detects_login_challenge_before_llm_call():
    script = {
        "navigate": {"url": "https://x.test"},
        "read_page": {"text": "Please sign in to continue"},
        "finish": {"payload": {"status": "login_challenge"}},
    }
    client = httpx.AsyncClient(transport=_mock_transport_handler(script))
    provider = _StubProvider([])
    result = await run(
        provider=provider,
        agent_browser_url="http://sidecar",
        source="dat",
        start_url="https://x.test",
        session_id="s1",
        allowlist=["https://x.test"],
        caps=AgentCaps(max_steps=5),
        http_client=client,
    )
    await client.aclose()
    assert result.status == "login_challenge"
    assert result.loads == []


async def test_run_hits_max_steps_cap_exceeded():
    # Script a scroll loop that never calls finish.
    script = {
        "navigate": {"url": "https://x.test"},
        "read_page": {"text": "trucks and lanes"},
        "scroll": {"scrolled": 1},
    }
    client = httpx.AsyncClient(transport=_mock_transport_handler(script))
    scrolls = ['{"tool":"scroll","args":{"pages":1}}'] * 20
    provider = _StubProvider(scrolls)
    result = await run(
        provider=provider,
        agent_browser_url="http://sidecar",
        source="dat",
        start_url="https://x.test",
        session_id="s2",
        allowlist=["https://x.test"],
        caps=AgentCaps(max_steps=3, max_input_tokens=99999, max_output_tokens=99999),
        http_client=client,
    )
    await client.aclose()
    assert result.status == "cap_exceeded"
    assert result.loads == []


async def test_run_rejects_url_outside_allowlist():
    script = {
        "navigate": {"url": "https://x.test"},
        "read_page": {"text": "trucks and lanes"},
    }
    client = httpx.AsyncClient(transport=_mock_transport_handler(script))
    provider = _StubProvider(['{"tool":"navigate","args":{"url":"https://evil.test/x"}}'])
    result = await run(
        provider=provider,
        agent_browser_url="http://sidecar",
        source="dat",
        start_url="https://x.test",
        session_id="s3",
        allowlist=["https://x.test"],
        caps=AgentCaps(max_steps=5),
        http_client=client,
    )
    await client.aclose()
    assert result.status == "error"
    assert result.error == "url_not_in_allowlist"


async def test_run_returns_loads_on_finish():
    script = {
        "navigate": {"url": "https://x.test"},
        "read_page": {"text": "lanes list here"},
    }
    client = httpx.AsyncClient(transport=_mock_transport_handler(script))
    finish_payload = {
        "tool": "finish",
        "args": {"payload": {"loads": [
            {"broker_name": "Acme", "origin_state": "VA", "dest_state": "GA"}
        ]}}
    }
    provider = _StubProvider([json.dumps(finish_payload)])
    result = await run(
        provider=provider,
        agent_browser_url="http://sidecar",
        source="dat",
        start_url="https://x.test",
        session_id="s4",
        allowlist=["https://x.test"],
        caps=AgentCaps(max_steps=5),
        http_client=client,
    )
    await client.aclose()
    assert result.status == "ok"
    assert len(result.loads) == 1
    assert result.loads[0].broker_name == "Acme"
