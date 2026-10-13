"""Sanitize + compact mail bodies at ingest.

Real-world mailbox data (849 rows locally, 15 MB total):
  * ~60 ``body_text`` entries carry raw quoted-printable (``=3D``, ``=20``,
    soft line breaks) because the sender's text/plain part never got
    decoded upstream.
  * A handful carry raw HTML entities (``&amp;``, ``&nbsp;``) in text/plain.
  * ~180 rows have an empty ``body_text`` but a full ``body_html`` — the
    message had no text/plain alternative at all.

What we do about it, in one pass at ingest and again (idempotently) in
the compact-bodies script:

* ``clean_html`` — strip ``<script>``, ``<style>``, ``<head>``,
  ``<meta>``, ``<link>``, ``<title>``, HTML comments, 1x1/base64 tracking
  pixels. Keep anchors, basic formatting, headings, lists. Collapse
  runs of whitespace. No raw HTML enters the DB.

* ``clean_text`` — decode quoted-printable (soft line breaks + ``=NN``),
  unescape HTML entities, normalise ``\\r\\n`` to ``\\n``, strip trailing
  whitespace, collapse ``\\n{3,}`` to a double newline.

* ``derive_text_from_html`` — when the message carries no text/plain at
  all, pull a readable fallback from the (already-sanitised) HTML with
  ``trafilatura`` (already a dep). Keeps triage's ``body_text`` reads
  working on html-only messages without pulling in a new dependency.

Stdlib-only on the HTML side: a focused ``html.parser.HTMLParser`` beats
adding ``bleach``/``nh3`` for a tightly bounded whitelist, and
``trafilatura`` is the one html-shaped dep we already have.
"""

from __future__ import annotations

import logging
import quopri
import re
from html import unescape
from html.parser import HTMLParser

log = logging.getLogger(__name__)


# Tags whose entire subtree we drop (content never meant for the reader).
_DROP_SUBTREE_TAGS = frozenset(
    {"script", "style", "head", "meta", "link", "title", "noscript", "object", "embed", "iframe"}
)

# Tags we keep verbatim (open+close+content). ``img`` is kept conditionally
# (see ``_is_tracking_img``) with an empty-pixel placeholder.
_ALLOWED_TAGS = frozenset(
    {
        "a", "abbr", "b", "blockquote", "br", "code", "div", "em", "font",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "li", "ol", "p",
        "pre", "s", "small", "span", "strong", "sub", "sup", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
    }
)

# Allowed attributes per tag — tight by design.
_ALLOWED_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "title"}),
    "td": frozenset({"colspan", "rowspan"}),
    "th": frozenset({"colspan", "rowspan"}),
}

# Void elements never get an end tag, so they must not open a "drop
# subtree" depth (an unclosed <meta>/<link> in <head> would otherwise
# swallow the whole document).
_VOID_DROP_TAGS = frozenset({"meta", "link", "embed"})

_URL_SAFE_SCHEMES = ("http://", "https://", "mailto:", "tel:")


def _is_tracking_img(attrs: dict[str, str]) -> bool:
    """1x1 pixels, base64 data: URIs, or empty src — all pure tracking."""
    src = (attrs.get("src") or "").strip().lower()
    if not src:
        return True
    if src.startswith("data:"):
        return True
    try:
        w = int(attrs.get("width", "") or "0")
        h = int(attrs.get("height", "") or "0")
        if 0 < w <= 2 and 0 < h <= 2:
            return True
    except ValueError:
        pass
    return False


class _Sanitizer(HTMLParser):
    """Rewrite a document to the whitelist, dropping unsafe subtrees."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._drop_depth = 0  # inside a dropped subtree

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _VOID_DROP_TAGS:
            return
        if tag in _DROP_SUBTREE_TAGS:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return
        attr_map = {k.lower(): (v or "") for k, v in attrs}
        if tag == "img":
            if _is_tracking_img(attr_map):
                return
            # non-tracking inline image → omit (we don't rehost); drop
            # entirely rather than leak an external url.
            return
        if tag not in _ALLOWED_TAGS:
            return
        kept: list[str] = []
        if tag in _ALLOWED_ATTRS:
            for k, v in attr_map.items():
                if k not in _ALLOWED_ATTRS[tag]:
                    continue
                if k == "href":
                    low = v.strip().lower()
                    if not low.startswith(_URL_SAFE_SCHEMES):
                        continue
                kept.append(f'{k}="{_escape_attr(v)}"')
        attrs_s = (" " + " ".join(kept)) if kept else ""
        self.out.append(f"<{tag}{attrs_s}>")

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _VOID_DROP_TAGS:
            return
        if tag in _DROP_SUBTREE_TAGS:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return
        if tag in _ALLOWED_TAGS and tag not in {"br", "hr"}:
            self.out.append(f"</{tag}>")

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        # <br/>, <hr/>, etc. A self-closed drop tag (<script/>) must not
        # leak depth: close it in the same step.
        self.handle_starttag(tag, attrs)
        if tag.lower() in _DROP_SUBTREE_TAGS and tag.lower() not in _VOID_DROP_TAGS:
            self.handle_endtag(tag)

    def handle_data(self, data: str) -> None:
        if self._drop_depth:
            return
        self.out.append(_escape_text(data))

    def handle_comment(self, data: str) -> None:
        return

    def handle_entityref(self, name: str) -> None:
        # convert_charrefs=True handles most, but guard anyway.
        self.out.append(_escape_text(unescape(f"&{name};")))

    def handle_charref(self, name: str) -> None:
        self.out.append(_escape_text(unescape(f"&#{name};")))


def _escape_text(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _escape_attr(s: str) -> str:
    return s.replace("&", "&amp;").replace('"', "&quot;").replace("<", "&lt;")


_WS_RUN = re.compile(r"[ \t\f\v]+")
_NEWLINES = re.compile(r"\n{3,}")


def clean_html(html: str | None) -> str:
    """Whitelist-sanitised HTML, with tracking artefacts stripped.

    Idempotent — running it on already-cleaned HTML is a no-op (the
    walk re-emits the same whitelisted tags with no further to drop).
    """
    if not html:
        return ""
    try:
        p = _Sanitizer()
        p.feed(html)
        p.close()
        out = "".join(p.out)
    except Exception:
        # A sanitiser that explodes must never corrupt a row — strip to
        # text so the record survives.
        log.exception("mail/sanitize: HTML parse failed, falling back to text strip")
        return _escape_text(_strip_tags_naive(html))
    # Collapse horizontal whitespace runs and vertical blank runs.
    out = _WS_RUN.sub(" ", out)
    out = _NEWLINES.sub("\n\n", out)
    return out.strip()


_QP_SOFT_BREAK = re.compile(r"=\r?\n")
# ``=NN`` hex followed by anything (quopri handles decoding) — we probe
# cheaply to avoid round-tripping clean text through quopri.
# QP mandates UPPERCASE hex, so a lowercase ``=ab`` in a URL query
# (``?token=deadbeef``) is NOT an escape. Require a soft line break or a
# high-signal escape (=20 =3D =0A =0D =09 =A0 =C2 =C3 =E2) to decode.
_QP_SIGNAL = re.compile(r"=\r?\n|=(?:20|3D|0A|0D|09|A0|C2|C3|E2)")


def _decode_qp_if_needed(text: str) -> str:
    """Decode quoted-printable only when the text actually looks like QP."""
    if not _QP_SIGNAL.search(text):
        return text
    try:
        # ``quopri.decodestring`` returns bytes; the message's charset is
        # already resolved upstream (``_decode_part`` in mailbox.py), so
        # a utf-8 round-trip is safe.
        return quopri.decodestring(text.encode("utf-8", errors="replace")).decode(
            "utf-8", errors="replace"
        )
    except Exception:
        log.exception("mail/sanitize: quopri decode failed; keeping original")
        return text


def clean_text(text: str | None) -> str:
    """Decode QP, unescape entities, normalise whitespace. Idempotent."""
    if not text:
        return ""
    s = _decode_qp_if_needed(text)
    # Entities first (``&amp;`` → ``&``), then collapse CRLFs.
    s = unescape(s)
    s = s.replace("\r\n", "\n").replace("\r", "\n")
    # Trim trailing whitespace per line and shrink blank-line runs.
    s = "\n".join(line.rstrip() for line in s.split("\n"))
    s = _NEWLINES.sub("\n\n", s)
    return s.strip()


_TAG_RE = re.compile(r"<[^>]+>")


def _strip_tags_naive(html: str) -> str:
    return unescape(_TAG_RE.sub(" ", html))


def derive_text_from_html(html: str | None) -> str:
    """Pull a readable text fallback out of (sanitised) HTML.

    Prefers ``trafilatura`` (already a dep); falls back to a naive strip.
    Only used when ``body_text`` is empty on ingest — never overrides a
    real text/plain part.
    """
    if not html:
        return ""
    try:
        import trafilatura  # type: ignore[import-not-found]

        extracted = trafilatura.extract(html, include_tables=True, favor_recall=True)
        if extracted:
            return clean_text(extracted)
    except Exception:
        log.exception("mail/sanitize: trafilatura extract failed; falling back")
    return clean_text(_strip_tags_naive(html))


def compact_bodies(body_text: str | None, body_html: str | None) -> tuple[str, str]:
    """Pair-wise cleanup — returns ``(text, html)`` ready to persist.

    Guarantees: text is non-empty whenever the pair carries any content
    (text wins if present; else trafilatura-derived from html).
    """
    html_clean = clean_html(body_html)
    text_clean = clean_text(body_text)
    if not text_clean and html_clean:
        text_clean = derive_text_from_html(html_clean)
    return text_clean, html_clean
