"""Mailbox source — read side of the Gmail connector.

Mirror of the sender factory: simulated by default, Gmail when env is set
and the DWD creds refresh cleanly. The actual Gmail path depends on
``google-api-python-client`` at runtime; a missing library drops back to
simulated.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from app.config import Settings
from app.integrations.adapters.email.credentials import build_delegated_credentials, load_sa_info
from app.integrations.adapters.email.ratelimit import MailboxLimiter

log = logging.getLogger(__name__)


@dataclass
class RawMessage:
    message_id: str
    thread_id: str
    history_id: str
    mailbox: str
    from_addr: str
    to_addrs: list[str]
    cc_addrs: list[str]
    subject: str
    sent_at: datetime
    received_at: datetime
    in_reply_to: str | None
    references: list[str]
    body_text: str
    body_html: str
    labels: list[str]
    raw: dict


class MailboxSource(Protocol):
    kind: str

    async def list_mailboxes(self) -> list[str]: ...

    async def backfill(self, mailbox: str, since: datetime) -> AsyncIterator[RawMessage]: ...

    async def incremental(self, mailbox: str, start_history_id: str) -> AsyncIterator[tuple[RawMessage, str]]: ...


@dataclass
class SimulatedMailbox:
    """A deterministic in-memory fixture source so inbox-analysis can be built
    without live Workspace access.
    """

    kind: str = "simulated"
    messages: dict[str, list[RawMessage]] = field(default_factory=dict)

    async def list_mailboxes(self) -> list[str]:
        return list(self.messages.keys())

    async def backfill(self, mailbox: str, since: datetime) -> AsyncIterator[RawMessage]:
        for m in self.messages.get(mailbox, []):
            if m.sent_at >= since:
                yield m

    async def incremental(self, mailbox: str, start_history_id: str) -> AsyncIterator[tuple[RawMessage, str]]:
        try:
            start = int(start_history_id)
        except ValueError:
            start = 0
        for m in self.messages.get(mailbox, []):
            try:
                hid = int(m.history_id)
            except ValueError:
                continue
            if hid > start:
                yield m, m.history_id


class GmailMailbox:
    """Real Gmail/Admin SDK source. Only live when env + google libs are present."""

    kind: str = "gmail"

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._limiter = MailboxLimiter(settings.gmail_per_mailbox_rps, settings.gmail_global_rps)
        self._sa = load_sa_info(settings.gmail_sa_json.get_secret_value() if settings.gmail_sa_json else None)

    async def list_mailboxes(self) -> list[str]:
        if self._sa is None or not self._settings.gmail_admin_impersonate:
            return []
        creds = build_delegated_credentials(
            self._sa, self._settings.gmail_admin_impersonate, list(self._settings.gmail_scopes_admin)
        )
        if creds is None:
            return []
        try:
            import anyio
            from googleapiclient.discovery import build  # type: ignore[import-not-found]

            def _call():
                svc = build("admin", "directory_v1", credentials=creds, cache_discovery=False)
                mailboxes: list[str] = []
                req = svc.users().list(customer="my_customer", maxResults=200)
                while req is not None:
                    resp = req.execute()
                    for u in resp.get("users", []) or []:
                        if u.get("primaryEmail"):
                            mailboxes.append(u["primaryEmail"])
                    req = svc.users().list_next(req, resp)
                return mailboxes

            return await anyio.to_thread.run_sync(_call)
        except Exception as exc:  # noqa: BLE001
            log.warning("mail/gmail: list_mailboxes failed: %s", exc)
            return []

    async def backfill(self, mailbox: str, since: datetime) -> AsyncIterator[RawMessage]:
        """Not implemented in v1 — enabled on access day."""
        if False:  # pragma: no cover - keeps function an async generator
            yield  # type: ignore[unreachable]
        log.info("mail/gmail: backfill stub for %s since %s", mailbox, since.isoformat())
        return

    async def incremental(self, mailbox: str, start_history_id: str) -> AsyncIterator[tuple[RawMessage, str]]:
        if False:  # pragma: no cover
            yield  # type: ignore[unreachable]
        log.info("mail/gmail: incremental stub for %s from %s", mailbox, start_history_id)
        return


def get_mailbox_source(settings: Settings) -> MailboxSource:
    if settings.mailbox_source != "gmail":
        return SimulatedMailbox()
    sa = load_sa_info(settings.gmail_sa_json.get_secret_value() if settings.gmail_sa_json else None)
    if sa is None:
        return SimulatedMailbox()
    return GmailMailbox(settings)


# Also expose a UTC "now" helper so stubs can be deterministic in tests.
def utc_now() -> datetime:
    return datetime.now(UTC)
