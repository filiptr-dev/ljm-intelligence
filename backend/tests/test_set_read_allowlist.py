"""Dry-run + diff behaviour for app.inbox.set_read_allowlist. The real
``run()`` boots a full engine via ``get_settings``, so here we exercise the
file reader + the CLI parser directly.
"""

from __future__ import annotations

import pytest

from app.inbox.set_read_allowlist import _parse_args, _read_file


def test_read_file_skips_blanks_and_comments(tmp_path) -> None:
    p = tmp_path / "list.txt"
    p.write_text(
        """
        # comment
        a@x.com
        B@X.com

        # trailing comment
        c@x.com
        """
    )
    assert _read_file(p) == ["a@x.com", "b@x.com", "c@x.com"]


def test_parse_args_requires_source() -> None:
    with pytest.raises(SystemExit):
        _parse_args([])


def test_parse_args_emails_or_file_is_exclusive(tmp_path) -> None:
    f = tmp_path / "list.txt"
    f.write_text("a@x.com\n")
    with pytest.raises(SystemExit):
        _parse_args(["--emails", "a@x.com", "--from-file", str(f)])


def test_parse_args_accepts_emails_only() -> None:
    args = _parse_args(["--emails", "a@x.com,b@x.com", "--dry-run"])
    assert args.emails == "a@x.com,b@x.com"
    assert args.dry_run is True
