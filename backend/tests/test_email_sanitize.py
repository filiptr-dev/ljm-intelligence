"""Email sanitize/compact — in-process unit tests. No DB.

Covers the three real-world ``mail_messages`` symptoms we saw in local:
QP artefacts, HTML entities, html-only (empty text) messages. Also
checks triage ``_classify`` does not diverge on cleaned text vs raw.
"""
from __future__ import annotations

from datetime import UTC, datetime

from app.inbox.triage import _classify
from app.integrations.adapters.email.mailbox import RawMessage
from app.integrations.adapters.email.sanitize import (
    clean_html,
    clean_text,
    compact_bodies,
    derive_text_from_html,
)


def _msg(subject: str, body_text: str) -> RawMessage:
    return RawMessage(
        message_id="m",
        thread_id="t",
        history_id="1",
        mailbox="contact@ljminternational.com",
        from_addr="broker@example.com",
        to_addrs=["contact@ljminternational.com"],
        cc_addrs=[],
        subject=subject,
        sent_at=datetime.now(UTC),
        received_at=datetime.now(UTC),
        in_reply_to=None,
        references=[],
        body_text=body_text,
        body_html="",
        labels=[],
        raw={},
    )


def test_clean_text_decodes_quoted_printable() -> None:
    raw = "Hi =3D team=\nThis =20is wrapped."  # soft break + QP escape
    cleaned = clean_text(raw)
    assert "=3D" not in cleaned
    assert "= team" in cleaned
    assert "This  is wrapped" in cleaned or "This is wrapped" in cleaned


def test_clean_text_unescapes_entities() -> None:
    assert clean_text("Hi &amp; bye &nbsp;then") == "Hi & bye \xa0then"


def test_clean_text_is_idempotent() -> None:
    once = clean_text("Hi =3D team=\n&amp;here")
    twice = clean_text(once)
    assert once == twice


def test_clean_html_drops_scripts_styles_pixels() -> None:
    html = (
        "<html><head><style>a{color:red}</style><title>x</title></head>"
        "<body><script>alert(1)</script>"
        '<img src="data:image/gif;base64,R0lGODlh" width="1" height="1">'
        '<p>Hello <a href="https://example.com">link</a></p>'
        "<!--tracker--></body></html>"
    )
    out = clean_html(html)
    assert "<script" not in out
    assert "<style" not in out
    assert "<img" not in out
    assert "<!--" not in out
    assert "<p>Hello" in out
    assert 'href="https://example.com"' in out


def test_clean_html_rejects_javascript_href() -> None:
    out = clean_html('<a href="javascript:evil()">x</a>')
    assert "javascript" not in out


def test_derive_text_from_html_pulls_readable_text() -> None:
    html = "<p>Hello <b>freight</b></p><p>Second line.</p>"
    text = derive_text_from_html(html)
    assert "Hello" in text
    assert "Second line" in text
    assert "<" not in text


def test_compact_bodies_fills_text_when_only_html() -> None:
    text, html = compact_bodies("", "<p>Load offer: NY to LA</p>")
    assert "Load offer" in text
    assert "<p>" in html


def test_compact_bodies_prefers_existing_text() -> None:
    text, _ = compact_bodies("plain-text wins", "<p>html</p>")
    assert text == "plain-text wins"


def test_triage_classify_stable_across_qp_cleanup() -> None:
    """Triage results must not change between the raw QP form and
    the cleaned form — the real symptom is noise, not meaning."""
    raw_body = (
        "Hi team=\nWe have a Van load picking up tomorrow. =20\n"
        "Target rate =3D $2200. Lane: Los Angeles, CA to Dallas, TX.=\n"
    )
    cleaned_body = clean_text(raw_body)

    r_raw = _classify(_msg("Load offer tomorrow", raw_body))
    r_clean = _classify(_msg("Load offer tomorrow", cleaned_body))
    assert r_raw.intent == r_clean.intent
    assert r_raw.sentiment == r_clean.sentiment
