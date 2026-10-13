"""send_new_email stores the sent mail under Gmail's real threadId."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.inbox import service
from app.inbox.models import MailMessage, NoReplyTracker
from app.integrations.adapters.email.sender import SendResult


async def _run(result: SendResult) -> tuple[dict, list]:
    session = MagicMock()
    added: list = []
    session.add.side_effect = added.append
    sender = SimpleNamespace(send=AsyncMock(return_value=result))
    settings = SimpleNamespace(outreach_postal_address="1 Main St", outreach_from_email="contact@ljm.test")
    with (
        patch.object(service, "_render_and_wrap", AsyncMock(return_value=("t", "<p>t</p>", {}))),
        patch("app.integrations.mail_service.effective_mode", AsyncMock(return_value="gmail")),
        patch("app.integrations.adapters.email.sender.get_mail_sender", return_value=sender),
    ):
        out = await service.send_new_email(
            session, to="a@b.co", subject="s", body_text="t", body_html="", settings=settings,
        )
    return out, added


@pytest.mark.asyncio
async def test_real_send_uses_gmail_thread_id() -> None:
    out, added = await _run(SendResult(message_id="m1", thread_id="gthread1", mode="real"))
    msg = next(a for a in added if isinstance(a, MailMessage))
    tracker = next(a for a in added if isinstance(a, NoReplyTracker))
    assert out["thread_id"] == msg.thread_id == tracker.thread_id == "gthread1"


@pytest.mark.asyncio
async def test_simulated_send_keeps_synthetic_thread_id() -> None:
    out, added = await _run(SendResult(message_id=None, thread_id=None, mode="simulated"))
    assert out["thread_id"].startswith("t-")
    assert all(a.thread_id == out["thread_id"] for a in added)
