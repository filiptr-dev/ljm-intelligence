"""House-style few-shot examples for AI drafts.

Pulls up to 4 of LJM's own past *sent* emails and formats them as a
style-only few-shot block the prompt callers append. Style, not content:
quoted replies + signature blocks are stripped, email / phone / $ amounts
are masked, and non-same-recipient greetings get `{Name}` substituted.

Gated by the tenant feature flag ``ai.match_style`` (default True). Any
exception bubbles up as "no examples" — style must never break drafting.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from sqlalchemy import desc, select

from app.inbox.models import MailMessage
from app.shared.db import AsyncSession

_l = logging.getLogger(__name__)

_LJM_MAILBOXES = ("contact@ljminternational.com", "viktor@ljminternational.com")

_SIGN_OFF_RE = re.compile(
    r"^\s*(thanks|thank you|regards|best regards|best|cheers|sincerely|"
    r"— ljm|-- ljm|— nick|regards,|best,)\b",
    re.IGNORECASE,
)

_QUOTE_CUT_RE = re.compile(
    r"^(on .{3,}wrote:|-----original message-----|from:\s|sent:\s|>+ )",
    re.IGNORECASE,
)

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_PHONE_RE = re.compile(r"\+?\d[\d\s().-]{7,}\d")
_MONEY_RE = re.compile(r"\$\s*\d[\d,]*(?:\.\d+)?")
_GREETING_RE = re.compile(r"^(hi|hello|hey|dear)\s+([A-Z][a-zA-Z'`\-]+)", re.IGNORECASE)


def strip_quoted_and_signature(body: str) -> str:
    """Cut quoted history + a trailing signature. Keeps the sign-off word."""
    if not body:
        return ""
    lines = body.replace("\r\n", "\n").split("\n")

    # 1. Cut at first quoted-history marker.
    cut = len(lines)
    for i, ln in enumerate(lines):
        if _QUOTE_CUT_RE.match(ln.strip()):
            cut = i
            break
    lines = lines[:cut]

    # 2. Trim a trailing signature block. We find the first line matching a
    #    sign-off word, then keep everything up to and including that line
    #    IF what follows looks like a signature (short lines, a name, phone,
    #    email, address). Keep the sign-off word itself since it's style.
    end = len(lines)
    for i in range(len(lines) - 1, -1, -1):
        if _SIGN_OFF_RE.match(lines[i].strip()):
            # Keep the sign-off line; drop whatever came after it (if any
            # following lines look like signature material).
            trailing = [ln.strip() for ln in lines[i + 1 : i + 6]]
            # If >= 1 trailing non-empty line looks like signature material,
            # drop everything after sign-off.
            sig_hits = sum(
                1 for t in trailing
                if t and (
                    _EMAIL_RE.search(t) or _PHONE_RE.search(t)
                    or len(t) < 40
                )
            )
            if sig_hits >= 1 or not any(trailing):
                end = i + 1
            break
    lines = lines[:end]

    # Trim trailing blank lines.
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines).strip()


def sanitize_example(text: str, *, same_recipient: bool) -> str:
    """Mask facts, trim to ~600 chars, and anonymise greeting name when not
    writing to the same recipient."""
    s = (text or "").strip()
    if not s:
        return ""
    s = _EMAIL_RE.sub("{email}", s)
    s = _PHONE_RE.sub("{phone}", s)
    s = _MONEY_RE.sub("${amount}", s)
    if not same_recipient:
        s = _GREETING_RE.sub(lambda m: f"{m.group(1)} {{Name}}", s)
    if len(s) > 600:
        s = s[:600].rstrip() + "…"
    return s


def _is_eligible_labels(labels: Any) -> bool:
    """Keep rows with ``SENT`` label and no ``mode:*`` / lowercase ``sent``.

    Lowercase ``sent`` and ``mode:*`` labels are stamped by the app's own
    simulated-sender path; we don't want to teach the AI to copy its own
    output.
    """
    if not isinstance(labels, (list, tuple)):
        return False
    saw_sent = False
    for raw in labels:
        if not isinstance(raw, str):
            continue
        if raw == "SENT":
            saw_sent = True
            continue
        if raw.startswith("mode:"):
            return False
        if raw == "sent":
            return False
    return saw_sent


async def fetch_style_examples(
    session: AsyncSession,
    *,
    to_email: str | None,
    mailbox: str | None = None,
    limit: int = 4,
) -> list[str]:
    """Return up to ``limit`` cleaned, masked example bodies (style only).

    Tier order: (1) same recipient email or same recipient domain,
    (2) same ``mailbox``, (3) recent from the two LJM mailboxes.
    Deduped by body signature, stops at ``limit``.
    """
    to = (to_email or "").strip().lower()
    to_domain = to.rsplit("@", 1)[1] if "@" in to else ""

    out: list[str] = []
    seen_sig: set[str] = set()

    async def _tier(where_fn, same_recipient: bool, over: int = 200) -> None:
        if len(out) >= limit:
            return
        q = (
            select(MailMessage)
            .where(*where_fn())
            .order_by(desc(MailMessage.sent_at))
            .limit(over)
        )
        rows = (await session.execute(q)).scalars().all()
        for r in rows:
            if len(out) >= limit:
                return
            if not _is_eligible_labels(r.labels):
                continue
            cleaned = strip_quoted_and_signature(r.body_text or "")
            if len(cleaned) < 40:
                continue
            masked = sanitize_example(cleaned, same_recipient=same_recipient)
            sig = masked[:120]
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            out.append(masked)

    # Tier 1 — same recipient or same domain.
    if to:
        def _w1():
            # to_addrs is a JSON array — we filter in Python below rather
            # than wire a dialect-specific JSON check.
            # So this tier simply pulls the most recent sent and we filter.
            return [MailMessage.mailbox.in_(_LJM_MAILBOXES)]

        # Pull a wider slice, filter in Python by to_addrs.
        q = (
            select(MailMessage)
            .where(MailMessage.mailbox.in_(_LJM_MAILBOXES))
            .order_by(desc(MailMessage.sent_at))
            .limit(300)
        )
        rows = (await session.execute(q)).scalars().all()
        for r in rows:
            if len(out) >= limit:
                break
            if not _is_eligible_labels(r.labels):
                continue
            to_addrs = [str(x).lower() for x in (r.to_addrs or []) if x]
            match = any(a == to for a in to_addrs) or (
                to_domain and any(a.endswith("@" + to_domain) for a in to_addrs)
            )
            if not match:
                continue
            cleaned = strip_quoted_and_signature(r.body_text or "")
            if len(cleaned) < 40:
                continue
            masked = sanitize_example(cleaned, same_recipient=True)
            sig = masked[:120]
            if sig in seen_sig:
                continue
            seen_sig.add(sig)
            out.append(masked)

    # Tier 2 — same mailbox.
    if mailbox and len(out) < limit:
        await _tier(lambda: [MailMessage.mailbox == mailbox], same_recipient=False)

    # Tier 3 — recent LJM sent mail.
    if len(out) < limit:
        await _tier(lambda: [MailMessage.mailbox.in_(_LJM_MAILBOXES)], same_recipient=False)

    return out


def style_block(examples: list[str]) -> str:
    """Format the examples as a prompt section. Empty when no examples."""
    if not examples:
        return ""
    parts = [
        "\n--- LJM's own past emails — style reference only ---",
        "Imitate ONLY the voice: greeting, sign-off, length, formality, sentence rhythm.",
        "Do NOT copy any facts, names, prices, lanes, dates or numbers from them.",
        "Use only facts from the task above.",
    ]
    for i, ex in enumerate(examples, 1):
        parts.append(f"\n--- example {i} ---\n{ex}")
    parts.append("--- end examples ---\n")
    return "\n".join(parts)


async def style_enabled(session: AsyncSession) -> bool:
    """Read the ``ai.match_style`` tenant flag; default ON when absent."""
    try:
        from app.identity.models import TenantFeatureFlag
        from app.shared.orm import LJM_TENANT_ID

        row = (
            await session.execute(
                select(TenantFeatureFlag).where(
                    TenantFeatureFlag.tenant_id == LJM_TENANT_ID,
                    TenantFeatureFlag.flag == "ai.match_style",
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return True
        v = row.value
        if isinstance(v, dict):
            return bool(v.get("enabled", True))
        return bool(v)
    except Exception as exc:  # noqa: BLE001 — flag read must never break drafting.
        _l.info("style_enabled: lookup failed: %s", type(exc).__name__)
        return True


async def build_style_block(
    session: AsyncSession | None,
    *,
    to_email: str | None,
    mailbox: str | None = None,
    limit: int = 4,
) -> str:
    """Convenience: read flag → fetch examples → format. Empty on any failure.

    When ``session`` is None (no DB access, e.g. compose with no session yet),
    returns ``""``.
    """
    if session is None:
        return ""
    try:
        if not await style_enabled(session):
            return ""
        examples = await fetch_style_examples(
            session, to_email=to_email, mailbox=mailbox, limit=limit,
        )
        return style_block(examples)
    except Exception as exc:  # noqa: BLE001 — style must never break drafting.
        _l.info("build_style_block: failed, no examples: %s", type(exc).__name__)
        return ""
