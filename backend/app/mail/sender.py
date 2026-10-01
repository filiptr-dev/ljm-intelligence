"""MailSender protocol + Simulated + Gmail implementations.

Fail-open to simulated: a bad credential never black-holes outreach — the
factory returns ``SimulatedSender`` and surfaces the reason in ``/mail/status``.
"""

from __future__ import annotations

import base64
import logging
from dataclasses import dataclass, field
from email.message import EmailMessage
from typing import Literal, Protocol

import anyio

from app.config import Settings
from app.mail.credentials import build_delegated_credentials, load_sa_info, test_refresh

log = logging.getLogger(__name__)

Mode = Literal["real", "simulated"]


@dataclass
class SendResult:
    message_id: str | None
    thread_id: str | None
    mode: Mode
    error: str | None = None


class MailSender(Protocol):
    kind: str

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
        body_html: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        thread_id: str | None = None,
        from_addr: str | None = None,
    ) -> SendResult: ...


@dataclass
class SimulatedSender:
    kind: str = "simulated"
    _sent: list[dict] = field(default_factory=list)

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
        body_html: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        thread_id: str | None = None,
        from_addr: str | None = None,
    ) -> SendResult:
        self._sent.append({"to": to, "subject": subject, "body": body, "headers": headers or {}})
        log.info("mail/simulated: pretend-send to=%s subject=%r", to, subject)
        return SendResult(message_id=None, thread_id=None, mode="simulated")


def _build_mime(
    *,
    from_addr: str,
    to: str,
    subject: str,
    body: str,
    body_html: str | None,
    headers: dict[str, str] | None,
    in_reply_to: str | None,
    references: list[str] | None,
) -> bytes:
    msg = EmailMessage()
    msg["From"] = from_addr
    msg["To"] = to
    msg["Subject"] = subject
    if in_reply_to:
        msg["In-Reply-To"] = in_reply_to
    if references:
        msg["References"] = " ".join(references)
    for k, v in (headers or {}).items():
        if v is None:
            continue
        if k in msg:
            del msg[k]
        msg[k] = v
    msg.set_content(body or "")
    if body_html:
        msg.add_alternative(body_html, subtype="html")
    return msg.as_bytes()


class GmailSender:
    """Real Gmail transport via DWD-impersonated service-account."""

    kind: str = "gmail"

    def __init__(self, creds, from_addr: str) -> None:
        self._creds = creds
        self._from = from_addr
        self._service = None

    def _build_service(self):
        if self._service is not None:
            return self._service
        from googleapiclient.discovery import build  # type: ignore[import-not-found]

        self._service = build("gmail", "v1", credentials=self._creds, cache_discovery=False)
        return self._service

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        headers: dict[str, str] | None = None,
        body_html: str | None = None,
        in_reply_to: str | None = None,
        references: list[str] | None = None,
        thread_id: str | None = None,
        from_addr: str | None = None,
    ) -> SendResult:
        mime = _build_mime(
            from_addr=from_addr or self._from,
            to=to,
            subject=subject,
            body=body,
            body_html=body_html,
            headers=headers,
            in_reply_to=in_reply_to,
            references=references,
        )
        raw = base64.urlsafe_b64encode(mime).decode("ascii")
        body_payload: dict = {"raw": raw}
        if thread_id:
            body_payload["threadId"] = thread_id

        def _call():
            svc = self._build_service()
            return svc.users().messages().send(userId="me", body=body_payload).execute()

        try:
            resp = await anyio.to_thread.run_sync(_call)
        except Exception as exc:
            log.exception("mail/gmail: send failed")
            return SendResult(message_id=None, thread_id=None, mode="simulated", error=str(exc))
        return SendResult(
            message_id=resp.get("id"),
            thread_id=resp.get("threadId"),
            mode="real",
        )


def get_mail_sender(settings: Settings, mode_override: str | None = None) -> MailSender:
    """Resolve the right sender. Fail-open to simulated on any failure.

    ``mode_override`` lets ``/settings`` flip the mode via the DB override
    without redeploying.
    """
    mode = mode_override or settings.mail_sender
    if mode != "gmail":
        return SimulatedSender()
    sa = load_sa_info(settings.gmail_sa_json.get_secret_value() if settings.gmail_sa_json else None)
    if sa is None:
        log.warning("mail/factory: GMAIL_SA_JSON missing/invalid; falling back to simulated")
        return SimulatedSender()
    scopes = list(settings.gmail_scopes_send)
    creds = build_delegated_credentials(sa, settings.gmail_impersonate, scopes)
    ok, reason = test_refresh(creds)
    if not ok:
        log.warning("mail/factory: token refresh failed (%s); falling back to simulated", reason)
        return SimulatedSender()
    return GmailSender(creds, from_addr=settings.outreach_from_email)
