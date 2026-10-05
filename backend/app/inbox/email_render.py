"""Server-side email renderer.

One function, two outputs: a Gmail-safe HTML (table-based, inline styles,
600px max width, web-safe fonts) and the plain-text alternative.

The HTML matches the on-screen `PreviewPanel` one-for-one:

  * optional logo header (absolute URL — see plan-gate note),
  * body paragraphs built from the plain-text body (newline-separated),
  * optional CTA button in ``design.accent_hex``,
  * optional dispatcher signature (name · title · company · fleet · phone · email),
  * CAN-SPAM footer with the postal address + unsubscribe link.

Why no `bleach` / `premailer` dep: the builder's body is a plain `textarea`
— the user never pastes HTML and we never accept HTML on the wire. All HTML
is generated here from the plain-text input, so we avoid the sanitiser +
inliner machinery entirely. If/when a WYSIWYG body lands, add both and
sanitise the paste.
"""

from __future__ import annotations

from dataclasses import dataclass
from html import escape


@dataclass(frozen=True)
class EmailDesignOut:
    """Shape we accept from the builder (see `inbox.router.EmailDesignIn`)."""

    accent_hex: str = "#BC2444"
    signature: bool = True
    logo: bool = True
    cta_label: str = ""
    cta_url: str = ""
    layout: str = "branded"  # plain | branded | card
    show_truck: bool = True


@dataclass(frozen=True)
class BrandBlock:
    """The dispatcher/company block the signature renders."""

    company: str = "LJM International"
    fleet: str = "North-American fleet"
    dispatcher: str = "Marko Trajkovski"
    phone: str = "+1 (312) 555-0144"
    email: str = "marko@ljminternational.com"
    logo_url: str = ""  # absolute URL to the public logo asset


DEFAULT_BRAND = BrandBlock()


def _paragraphs(body_text: str) -> list[str]:
    """Split on blank lines; each chunk becomes one <p>."""
    chunks: list[str] = []
    buf: list[str] = []
    for line in (body_text or "").splitlines():
        if line.strip() == "":
            if buf:
                chunks.append("\n".join(buf).strip())
                buf = []
        else:
            buf.append(line)
    if buf:
        chunks.append("\n".join(buf).strip())
    return [c for c in chunks if c]


def _button(label: str, url: str, accent_hex: str) -> str:
    if not label or not url:
        return ""
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin:16px 0 8px 0"><tr><td align="left" bgcolor="{accent_hex}" '
        f'style="border-radius:4px"><a href="{escape(url, quote=True)}" '
        f'style="display:inline-block;padding:10px 18px;color:#ffffff;'
        f'text-decoration:none;font-weight:600;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;line-height:1">{escape(label)}</a></td></tr></table>'
    )


def _signature(brand: BrandBlock, accent_hex: str) -> str:
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'style="margin-top:20px;border-top:3px solid {accent_hex};padding-top:10px">'
        f'<tr><td style="font-family:Arial,Helvetica,sans-serif;font-size:13px;'
        f'line-height:1.5;color:#2B2B2B">'
        f'<div style="font-weight:700">{escape(brand.dispatcher)}</div>'
        f'<div>{escape(brand.company)} · {escape(brand.fleet)}</div>'
        f'<div><a href="tel:{escape(brand.phone, quote=True)}" '
        f'style="color:#2B2B2B;text-decoration:none">{escape(brand.phone)}</a>'
        f' · <a href="mailto:{escape(brand.email, quote=True)}" '
        f'style="color:#2B2B2B;text-decoration:none">{escape(brand.email)}</a></div>'
        f"</td></tr></table>"
    )


def _logo_header(brand: BrandBlock, accent_hex: str) -> str:
    """Logo + brand bar at the top of the email."""
    if brand.logo_url:
        img = (
            f'<img src="{escape(brand.logo_url, quote=True)}" alt="{escape(brand.company)}" '
            f'width="140" height="36" style="display:block;border:0;outline:none;'
            f'text-decoration:none;max-width:140px;height:auto" />'
        )
    else:
        # Plain wordmark fallback — renders without image blocking.
        img = (
            f'<span style="font-family:Arial,Helvetica,sans-serif;font-size:18px;'
            f'font-weight:800;color:{accent_hex};letter-spacing:0.08em">'
            f"{escape(brand.company)}</span>"
        )
    return (
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'width="100%" style="margin-bottom:16px"><tr><td align="left" '
        f'style="padding:8px 0;border-bottom:1px solid #e5e7eb">{img}</td></tr></table>'
    )


def render_email(
    *,
    body_text: str,
    subject: str,
    design: EmailDesignOut,
    brand: BrandBlock | None = None,
    footer_html: str = "",
    footer_text: str = "",
) -> tuple[str, str]:
    """Build ``(html, text)`` from the builder inputs.

    ``footer_html`` / ``footer_text`` is the unsub + postal-address block,
    passed in by the service after it computes ``with_unsub_footer`` and
    the unsub headers.
    """
    br = brand or DEFAULT_BRAND
    paragraphs = _paragraphs(body_text)
    accent = design.accent_hex or "#BC2444"
    plain = design.layout == "plain"

    para_html = "".join(
        f'<p style="margin:0 0 12px 0;font-family:Arial,Helvetica,sans-serif;'
        f'font-size:14px;line-height:1.6;color:#2B2B2B">{escape(p).replace(chr(10), "<br />")}</p>'
        for p in paragraphs
    )
    cta_html = _button(design.cta_label, design.cta_url, accent) if not plain else ""
    sig_html = _signature(br, accent) if design.signature else ""
    logo_html = _logo_header(br, accent) if (design.logo and not plain) else ""

    footer_block = ""
    if footer_html:
        footer_block = (
            f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
            f'width="100%" style="margin-top:28px;border-top:1px solid #e5e7eb;'
            f'padding-top:12px"><tr><td style="font-family:Arial,Helvetica,sans-serif;'
            f'font-size:11px;line-height:1.5;color:#6b7280">{footer_html}</td></tr></table>'
        )

    inner = f"{logo_html}{para_html}{cta_html}{sig_html}{footer_block}"

    if design.layout == "card":
        inner = (
            f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
            f'width="100%" style="background:#ffffff;border:1px solid #e5e7eb;'
            f'border-radius:6px;padding:20px"><tr><td>{inner}</td></tr></table>'
        )

    html = (
        f"<!doctype html><html><head><meta charset=\"utf-8\" />"
        f"<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\" />"
        f"<title>{escape(subject)}</title></head>"
        f'<body style="margin:0;padding:0;background:#f5f6f8">'
        f'<table role="presentation" cellspacing="0" cellpadding="0" border="0" '
        f'width="100%" style="background:#f5f6f8"><tr><td align="center" '
        f'style="padding:20px"><table role="presentation" cellspacing="0" cellpadding="0" '
        f'border="0" width="600" style="max-width:600px;background:#ffffff;'
        f'padding:20px"><tr><td>{inner}</td></tr></table></td></tr></table>'
        f"</body></html>"
    )

    # ---- plain-text alternative, built from the same input.
    text_body = (body_text or "").strip()
    text_parts: list[str] = [text_body]
    if design.cta_label and design.cta_url and not plain:
        text_parts.append(f"\n{design.cta_label}: {design.cta_url}")
    if design.signature:
        text_parts.append(
            f"\n--\n{br.dispatcher}\n{br.company} · {br.fleet}\n{br.phone} · {br.email}"
        )
    if footer_text:
        text_parts.append(f"\n{footer_text}")
    text = "\n".join(text_parts).strip()
    return html, text
