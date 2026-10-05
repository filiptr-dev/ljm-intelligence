"""Scoped test for the Inbox /ask endpoint — AC4 of the inbox-ask-ai-fix plan.

Verifies the identity-fallback contract: when the AI provider is null
(no key wired), ``POST /inbox/ask`` returns 200 with all-null fields
and never 500s. The provider-hit path is covered by the shared
``inbox_draft_reply`` feature slot's existing tests.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient


@pytest.mark.asyncio
async def test_ask_endpoint_fallback() -> None:
    from app.main import create_app

    app = create_app()
    # Force the identity-fallback path by making the provider resolve to
    # ``None`` — exactly what happens on prod when no key is wired or on
    # transient resolve failure. The endpoint must still 200 with all-null.
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            with patch("app.integrations.adapters.ai.provider.get_for", return_value=None):
                r = await c.post(
                    "/inbox/ask",
                    json={"question": "which brokers complained about late delivery?"},
                )
    assert r.status_code == 200, r.text
    body = r.json()
    # Shape round-trips exactly.
    assert set(body) == {"intent", "keywords", "sentiment", "summary"}
    # Identity fallback: no provider wired → all-null hint.
    assert body["intent"] is None
    assert body["keywords"] == []
    assert body["sentiment"] is None
    assert body["summary"] == ""
