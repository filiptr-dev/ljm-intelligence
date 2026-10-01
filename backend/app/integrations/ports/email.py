"""Email mailbox + sender ports — one Protocol per direction of travel."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Protocol


class EmailMailboxPort(Protocol):
    """Read side — ingest from a mailbox (e.g. Gmail Service Account)."""
    async def list_messages(self, since: datetime, cursor: str | None) -> Any: ...
    async def fetch(self, message_id: str) -> Any: ...
    async def mark_processed(self, message_id: str) -> None: ...


class EmailSenderPort(Protocol):
    """Write side — deliver an outbound message."""
    async def send(
        self,
        to: str,
        subject: str,
        body_html: str,
        body_text: str,
        headers: dict[str, str],
    ) -> Any: ...
