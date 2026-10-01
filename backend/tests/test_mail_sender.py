"""Mail sender factory + SimulatedSender triangulation.

Three cases per the honeypot rule:

* ``MAIL_SENDER='simulated'`` (default) → ``SimulatedSender``.
* ``MAIL_SENDER='gmail'`` but ``GMAIL_SA_JSON`` absent → fail-open to simulated.
* ``MAIL_SENDER='gmail'`` + SA parses + ``test_refresh`` ok → ``GmailSender``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from pydantic import SecretStr

from app.config import GmailSettings, Settings
from app.integrations.adapters.email.sender import GmailSender, SimulatedSender, get_mail_sender


# Sentinel: `None` is a valid override value for `gmail_sa_json`, so we need a
# distinct marker for "not provided" that doesn't collide.
_UNSET = object()


def _settings(gmail_sa_json=_UNSET, **overrides) -> Settings:
    """Fold the legacy flat `gmail_sa_json` kwarg into the grouped `gmail`
    sub-settings so existing tests keep working after the settings-grouping
    refactor."""
    if gmail_sa_json is not _UNSET:
        overrides["gmail"] = GmailSettings(sa_json=gmail_sa_json)
    return Settings().model_copy(update=overrides)


@pytest.mark.asyncio
async def test_factory_defaults_to_simulated() -> None:
    sender = get_mail_sender(_settings())
    assert isinstance(sender, SimulatedSender)
    result = await sender.send(to="x@y", subject="s", body="b")
    assert result.mode == "simulated"
    assert result.message_id is None


def test_factory_fail_open_when_gmail_mode_but_no_sa() -> None:
    s = _settings(mail_sender="gmail", gmail_sa_json=None)
    assert isinstance(get_mail_sender(s), SimulatedSender)


def test_factory_returns_gmail_when_env_and_refresh_ok() -> None:
    sa_json = (
        '{"client_email":"sa@proj.iam.gserviceaccount.com","client_id":"123",'
        '"private_key":"KEY","type":"service_account"}'
    )
    s = _settings(mail_sender="gmail", gmail_sa_json=SecretStr(sa_json), mail_owner_send_enabled=True)
    with (
        patch("app.integrations.adapters.email.sender.build_delegated_credentials", return_value=object()),
        patch("app.integrations.adapters.email.sender.test_refresh", return_value=(True, None)),
    ):
        sender = get_mail_sender(s)
    assert isinstance(sender, GmailSender)
    assert sender.kind == "gmail"


def test_factory_fallback_when_refresh_fails() -> None:
    sa_json = (
        '{"client_email":"sa@proj.iam.gserviceaccount.com","client_id":"123",'
        '"private_key":"KEY","type":"service_account"}'
    )
    s = _settings(mail_sender="gmail", gmail_sa_json=SecretStr(sa_json), mail_owner_send_enabled=True)
    with (
        patch("app.integrations.adapters.email.sender.build_delegated_credentials", return_value=object()),
        patch("app.integrations.adapters.email.sender.test_refresh", return_value=(False, "boom")),
    ):
        sender = get_mail_sender(s)
    assert isinstance(sender, SimulatedSender)


def test_factory_fallback_when_owner_send_switch_off() -> None:
    """Even with Gmail configured, owner-send OFF means simulated."""
    sa_json = (
        '{"client_email":"sa@proj.iam.gserviceaccount.com","client_id":"123",'
        '"private_key":"KEY","type":"service_account"}'
    )
    s = _settings(mail_sender="gmail", gmail_sa_json=SecretStr(sa_json), mail_owner_send_enabled=False)
    sender = get_mail_sender(s)
    assert isinstance(sender, SimulatedSender)


@pytest.mark.asyncio
async def test_gmail_sender_builds_mime_and_sends() -> None:
    """``GmailSender.send`` passes a base64url-encoded MIME with all headers."""

    calls: dict = {}

    class _FakeExec:
        def __init__(self, body):
            self._body = body

        def execute(self):
            calls["body"] = self._body
            return {"id": "MID-1", "threadId": "TID-1"}

    class _FakeMessages:
        def send(self, *, userId, body):
            calls["userId"] = userId
            return _FakeExec(body)

    class _FakeUsers:
        def messages(self):
            return _FakeMessages()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    sender = GmailSender(creds=object(), from_addr="from@ljm")
    sender._service = _FakeService()
    result = await sender.send(
        to="to@ljm",
        subject="hi",
        body="hello world",
        body_html="<p>hello</p>",
        headers={"List-Unsubscribe": "<mailto:u@ljm>"},
        in_reply_to="<prev@ljm>",
        references=["<r1@ljm>"],
    )
    assert result.mode == "real"
    assert result.message_id == "MID-1"
    assert result.thread_id == "TID-1"
    assert calls["userId"] == "me"
    assert "raw" in calls["body"]
    # Decode a bit and sanity-check headers made it in.
    import base64

    raw = base64.urlsafe_b64decode(calls["body"]["raw"].encode()).decode()
    assert "List-Unsubscribe" in raw
    assert "In-Reply-To" in raw
    assert "References" in raw
    assert "to@ljm" in raw
    assert "from@ljm" in raw
