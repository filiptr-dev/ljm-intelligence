"""Unit tests for inbox.style_examples — the house-style few-shot helper.

Pure helpers (strip_quoted_and_signature / sanitize_example / style_block)
run offline; fetch_style_examples uses a tiny async in-memory ORM session
to avoid touching the dev DB.
"""

from __future__ import annotations

import pytest

from app.inbox.style_examples import (
    _is_eligible_labels,
    sanitize_example,
    strip_quoted_and_signature,
    style_block,
)


def test_strip_quoted_history():
    body = (
        "Hi John,\n\nThanks for the load. We can take it at $2,100.\n\n— LJM\n\n"
        "On Mon, Oct 10 2026, John <john@acme.com> wrote:\n"
        "> We have a load from Chicago to Dallas\n"
    )
    cleaned = strip_quoted_and_signature(body)
    assert "On Mon" not in cleaned
    assert "John <john@acme.com>" not in cleaned
    # Keep the body + sign-off
    assert "— LJM" in cleaned
    assert "Thanks for the load" in cleaned


def test_strip_signature_block_kept_signoff():
    body = (
        "Hi,\n\nTruck's on-site at 09:00.\n\nThanks,\n"
        "Nick Rivera\n+1 973-555-0100\nnick@ljminternational.com\n"
    )
    cleaned = strip_quoted_and_signature(body)
    assert "Thanks" in cleaned
    assert "973" not in cleaned
    assert "nick@ljminternational.com" not in cleaned


def test_sanitize_masks_and_renames():
    txt = "Hi Mark, we can run Chicago → Dallas for $2,500. Call me at 973-555-0100 or mark@acme.com. — LJM"
    s = sanitize_example(txt, same_recipient=False)
    assert "{email}" in s
    assert "{phone}" in s
    assert "${amount}" in s
    assert "Mark" not in s  # anonymised
    assert "{Name}" in s


def test_sanitize_keeps_name_for_same_recipient():
    txt = "Hi Mark, confirming today."
    s = sanitize_example(txt, same_recipient=True)
    assert "Mark" in s
    assert "{Name}" not in s


def test_sanitize_trims():
    txt = "x" * 1000
    s = sanitize_example(txt, same_recipient=False)
    assert len(s) <= 601  # 600 + the ellipsis


def test_labels_eligibility():
    assert _is_eligible_labels(["SENT"]) is True
    assert _is_eligible_labels(["SENT", "INBOX"]) is True
    # app's own simulated-sent — excluded
    assert _is_eligible_labels(["SENT", "mode:simulated"]) is False
    assert _is_eligible_labels(["sent"]) is False
    assert _is_eligible_labels([]) is False
    assert _is_eligible_labels("SENT") is False  # wrong shape


def test_style_block_empty_when_no_examples():
    assert style_block([]) == ""


def test_style_block_formats():
    s = style_block(["hello one", "hello two"])
    assert "--- example 1 ---" in s
    assert "--- example 2 ---" in s
    assert "Do NOT copy any facts" in s


@pytest.mark.asyncio
async def test_build_style_block_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.inbox import style_examples as se

    async def _false(_s):
        return False

    monkeypatch.setattr(se, "style_enabled", _false)
    out = await se.build_style_block(session=object(), to_email=None)
    assert out == ""
