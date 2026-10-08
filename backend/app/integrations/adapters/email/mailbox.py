"""Mailbox source — read side of the Gmail connector.

Mirror of the sender factory: simulated by default, Gmail when env is set
and the DWD creds refresh cleanly. The actual Gmail path depends on
``google-api-python-client`` at runtime; a missing library drops back to
simulated.

Real Gmail path:
  * ``backfill`` — ``users.messages.list`` + batched ``users.messages.get``,
    walked page-by-page via ``pageToken``. The caller's cursor is advanced
    per-message so a half-written backfill resumes on the next slice.
  * ``incremental`` — ``users.history.list`` from ``start_history_id``.
    If Gmail returns 404 "historyId not found" (the ~7-day retention cliff),
    we fall back to a ``messages.list`` window of the last ``REBACKFILL_DAYS``
    days so no mail is lost silently. The fallback flag is logged, never
    swallowed.
"""

from __future__ import annotations

import base64
import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Protocol

from app.config import Settings
from app.integrations.adapters.email.credentials import (
    build_delegated_credentials,
    resolve_impersonate,
    resolve_sa_info,
)
from app.integrations.adapters.email.ratelimit import MailboxLimiter

REBACKFILL_DAYS = 30
BATCH_PAGE_SIZE = 100

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


def _default_corpus() -> dict[str, list[RawMessage]]:
    """Lazy-load the whole-inbox demo corpus. Kept behind a function so tests
    that construct an empty ``SimulatedMailbox(messages={})`` still get an
    empty one."""
    from app.integrations.adapters.email.simulated_corpus import build_corpus

    return build_corpus()


@dataclass
class SimulatedMailbox:
    """A deterministic in-memory fixture source so inbox-analysis can be built
    without live Workspace access.

    When constructed without ``messages``, loads the whole-inbox demo corpus
    (``simulated_corpus.build_corpus()``) so the inbox-analysis UI has real
    data today, before DWD is granted.
    """

    kind: str = "simulated"
    messages: dict[str, list[RawMessage]] = field(default_factory=_default_corpus)

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
        self._limiter = MailboxLimiter(settings.gmail.per_mailbox_rps, settings.gmail.global_rps)
        # Env wins; else vault-primed cache (populated during app lifespan).
        self._sa = resolve_sa_info(settings)

    async def list_mailboxes(self) -> list[str]:
        impersonate = resolve_impersonate(self._settings)
        if self._sa is None or not impersonate:
            return []
        creds = build_delegated_credentials(
            self._sa, impersonate, list(self._settings.gmail.scopes_admin)
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

    def _service(self, mailbox: str):
        """Build a per-mailbox Gmail service with DWD impersonation."""
        if self._sa is None:
            return None
        creds = build_delegated_credentials(
            self._sa, mailbox, list(self._settings.gmail.scopes_read)
        )
        if creds is None:
            return None
        try:
            from googleapiclient.discovery import build  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover
            return None
        return build("gmail", "v1", credentials=creds, cache_discovery=False)

    async def backfill(self, mailbox: str, since: datetime) -> AsyncIterator[RawMessage]:
        """Paged, resumable, rate-limited backfill.

        Walks ``messages.list`` with ``q=after:YYYY/MM/DD``, hydrates each id
        via ``messages.get``. Each message is yielded as a RawMessage; the
        ingest layer owns the cursor so a queue-slice resume is cheap.
        """
        import anyio

        svc = self._service(mailbox)
        if svc is None:
            log.warning("mail/gmail: backfill cannot build service for %s", mailbox)
            return
        after = since.strftime("%Y/%m/%d")

        def _list_page(token: str | None) -> dict[str, Any]:
            req = svc.users().messages().list(  # type: ignore[union-attr]
                userId="me", q=f"after:{after}", maxResults=BATCH_PAGE_SIZE,
                pageToken=token, includeSpamTrash=False,
            )
            return req.execute()

        def _get_message(mid: str) -> dict[str, Any]:
            return (
                svc.users().messages().get(  # type: ignore[union-attr]
                    userId="me", id=mid, format="full"
                ).execute()
            )

        page_token: str | None = None
        while True:
            await self._limiter.acquire(mailbox)
            try:
                page = await anyio.to_thread.run_sync(_list_page, page_token)
            except Exception as exc:  # noqa: BLE001
                log.warning("mail/gmail: list page failed for %s: %s", mailbox, exc)
                return
            ids = [m["id"] for m in (page.get("messages") or [])]
            for mid in ids:
                await self._limiter.acquire(mailbox)
                try:
                    raw = await anyio.to_thread.run_sync(_get_message, mid)
                except Exception as exc:  # noqa: BLE001
                    log.warning("mail/gmail: get %s failed: %s", mid, exc)
                    continue
                parsed = _parse_gmail_message(raw, mailbox)
                if parsed is not None:
                    yield parsed
            page_token = page.get("nextPageToken")
            if not page_token:
                return

    async def incremental(
        self, mailbox: str, start_history_id: str
    ) -> AsyncIterator[tuple[RawMessage, str]]:
        """Pull everything since ``start_history_id`` via ``history.list``.

        On HTTP 404 (historyId expired past Gmail's ~7-day retention), fall
        back to a ``REBACKFILL_DAYS``-day backfill window so we never silently
        drop mail. The caller's cursor is advanced on every yield.
        """
        import anyio

        svc = self._service(mailbox)
        if svc is None:
            log.warning("mail/gmail: incremental cannot build service for %s", mailbox)
            return

        def _history_page(token: str | None) -> dict[str, Any]:
            req = svc.users().history().list(  # type: ignore[union-attr]
                userId="me", startHistoryId=start_history_id,
                historyTypes=["messageAdded"], pageToken=token,
            )
            return req.execute()

        def _get_message(mid: str) -> dict[str, Any]:
            return (
                svc.users().messages().get(  # type: ignore[union-attr]
                    userId="me", id=mid, format="full"
                ).execute()
            )

        page_token: str | None = None
        try:
            while True:
                await self._limiter.acquire(mailbox)
                page = await anyio.to_thread.run_sync(_history_page, page_token)
                for entry in page.get("history") or []:
                    for added in entry.get("messagesAdded") or []:
                        m = added.get("message") or {}
                        mid = m.get("id")
                        if not mid:
                            continue
                        await self._limiter.acquire(mailbox)
                        try:
                            raw = await anyio.to_thread.run_sync(_get_message, mid)
                        except Exception as exc:  # noqa: BLE001
                            log.warning("mail/gmail: get %s failed: %s", mid, exc)
                            continue
                        parsed = _parse_gmail_message(raw, mailbox)
                        if parsed is not None:
                            yield parsed, parsed.history_id
                page_token = page.get("nextPageToken")
                if not page_token:
                    return
        except Exception as exc:  # noqa: BLE001
            # historyId expired → re-backfill the last REBACKFILL_DAYS days.
            # Googleapiclient raises HttpError with resp.status == 404 for this.
            status = getattr(getattr(exc, "resp", None), "status", None)
            msg = str(exc)
            if status == 404 or "historyId" in msg or "startHistoryId" in msg:
                log.warning(
                    "mail/gmail: historyId expired for %s (%s) — falling back to %sd re-backfill",
                    mailbox, start_history_id, REBACKFILL_DAYS,
                )
                since = datetime.now(UTC) - timedelta(days=REBACKFILL_DAYS)
                async for m in self.backfill(mailbox, since):
                    yield m, m.history_id
                return
            log.warning("mail/gmail: incremental failed for %s: %s", mailbox, exc)
            return


def _header(headers: list[dict], name: str) -> str:
    name_lower = name.lower()
    for h in headers or []:
        if (h.get("name") or "").lower() == name_lower:
            return h.get("value") or ""
    return ""


def _decode_part(data: str | None) -> str:
    if not data:
        return ""
    try:
        return base64.urlsafe_b64decode(data.encode("ascii") + b"==").decode("utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _collect_bodies(payload: dict | None) -> tuple[str, str]:
    """Walk a Gmail payload tree, return (text, html). First found wins per mime."""
    text, html = "", ""
    if not payload:
        return text, html
    stack = [payload]
    while stack:
        node = stack.pop()
        mime = node.get("mimeType") or ""
        body = node.get("body") or {}
        data = body.get("data")
        if data and mime == "text/plain" and not text:
            text = _decode_part(data)
        elif data and mime == "text/html" and not html:
            html = _decode_part(data)
        stack.extend(node.get("parts") or [])
    return text, html


def _parse_dt(hdr: str) -> datetime:
    try:
        dt = parsedate_to_datetime(hdr)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:  # noqa: BLE001
        return datetime.now(UTC)


def _split_addrs(hdr: str) -> list[str]:
    if not hdr:
        return []
    out: list[str] = []
    for part in hdr.split(","):
        _, addr = parseaddr(part.strip())
        if addr:
            out.append(addr)
    return out


def _parse_gmail_message(raw: dict, mailbox: str) -> RawMessage | None:
    """Gmail API message → RawMessage. Returns None if payload is unusable."""
    mid = raw.get("id")
    tid = raw.get("threadId")
    hid = str(raw.get("historyId") or "0")
    if not mid or not tid:
        return None
    payload = raw.get("payload") or {}
    headers = payload.get("headers") or []
    body_text, body_html = _collect_bodies(payload)
    date_hdr = _header(headers, "Date")
    sent_at = _parse_dt(date_hdr) if date_hdr else datetime.fromtimestamp(
        int(raw.get("internalDate", "0") or 0) / 1000, tz=UTC
    )
    received_at = datetime.fromtimestamp(int(raw.get("internalDate", "0") or 0) / 1000, tz=UTC)
    _, from_addr = parseaddr(_header(headers, "From"))
    refs_hdr = _header(headers, "References")
    refs = [r for r in (refs_hdr.split() if refs_hdr else [])]
    return RawMessage(
        message_id=mid,
        thread_id=tid,
        history_id=hid,
        mailbox=mailbox,
        from_addr=from_addr or "",
        to_addrs=_split_addrs(_header(headers, "To")),
        cc_addrs=_split_addrs(_header(headers, "Cc")),
        subject=_header(headers, "Subject"),
        sent_at=sent_at,
        received_at=received_at,
        in_reply_to=(_header(headers, "In-Reply-To") or None),
        references=refs,
        body_text=body_text,
        body_html=body_html,
        labels=list(raw.get("labelIds") or []),
        raw={"id": mid, "snippet": raw.get("snippet", "")},
    )


def get_mailbox_source(settings: Settings) -> MailboxSource:
    if settings.mailbox_source != "gmail":
        return SimulatedMailbox()
    sa = resolve_sa_info(settings)
    if sa is None:
        return SimulatedMailbox()
    return GmailMailbox(settings)


# Also expose a UTC "now" helper so stubs can be deterministic in tests.
def utc_now() -> datetime:
    return datetime.now(UTC)
