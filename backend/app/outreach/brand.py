"""LJM branded email HTML template.

Why the logo strategy: hosting the SVG-derived logo from `frontend/public/ljm-logo.png`
means the frontend deploy (Vercel, HTTPS) serves it, which every modern email client
renders. If we ever inline base64, quoted-printable and Outlook both bloat and break —
a hosted PNG is the one path that Just Works across Gmail / Outlook / Apple Mail.
"""

from __future__ import annotations

from html import escape

BRAND_RED = "#BC2444"
CHARCOAL = "#2B2B2B"
TEXT = "#333333"
WHITE = "#FFFFFF"

LJM_ADDRESS = "22 Troy Lane, Lincoln Park, NJ 07035"
LJM_PHONE = "862-203-4274"
LJM_WEB = "ljminternational.com"
LJM_SENDER_DEFAULT = "LJM International"


def _p(text: str) -> str:
    """Escape a body of text into <p>-broken HTML, keeping paragraph breaks."""
    parts = [escape(chunk).replace("\n", "<br>") for chunk in text.strip().split("\n\n") if chunk.strip()]
    return "\n".join(f'<p style="margin:0 0 14px 0;line-height:1.55;">{p}</p>' for p in parts)


def render_branded_email(
    *,
    subject: str,
    body: str,
    frontend_origin: str | None = None,
    sender_name: str = LJM_SENDER_DEFAULT,
) -> str:
    """Return a full, inline-styled HTML email. Table-based so Outlook can render it."""
    # Ship the SVG that already lives in frontend/public. Gmail + Apple Mail render
    # HTTPS-hosted SVGs; for the strictest clients (older Outlook), convert this file
    # to PNG at deploy time and update this path — no code change needed, it's env-driven.
    logo_src = (
        (frontend_origin.rstrip("/") + "/ljm-intelligence.svg")
        if frontend_origin
        else "https://ljminternational.com/ljm-intelligence.svg"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <title>{escape(subject)}</title>
</head>
<body style="margin:0;padding:0;background:#f4f4f4;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;color:{TEXT};">
  <table role="presentation" width="100%" cellpadding="0" cellspacing="0" style="background:#f4f4f4;padding:24px 0;">
    <tr>
      <td align="center">
        <table role="presentation" width="600" cellpadding="0" cellspacing="0" style="max-width:600px;background:{WHITE};border:1px solid #e5e5e5;">
          <tr>
            <td style="background:{CHARCOAL};padding:20px 24px;" align="left">
              <img src="{escape(logo_src)}" alt="LJM International" height="36" style="display:block;height:36px;border:0;outline:none;">
            </td>
          </tr>
          <tr>
            <td style="height:4px;background:{BRAND_RED};font-size:0;line-height:0;">&nbsp;</td>
          </tr>
          <tr>
            <td style="padding:28px 32px 8px 32px;color:{TEXT};font-size:15px;">
              {_p(body)}
            </td>
          </tr>
          <tr>
            <td style="padding:0 32px 28px 32px;">
              <hr style="border:0;border-top:1px solid #e5e5e5;margin:12px 0 16px 0;">
              <p style="margin:0;font-size:12px;color:#666;line-height:1.55;">
                <strong style="color:{CHARCOAL};">{escape(sender_name)}</strong><br>
                {escape(LJM_ADDRESS)}<br>
                <a href="tel:{escape(LJM_PHONE)}" style="color:{BRAND_RED};text-decoration:none;">{escape(LJM_PHONE)}</a>
                &nbsp;·&nbsp;
                <a href="https://{escape(LJM_WEB)}" style="color:{BRAND_RED};text-decoration:none;">{escape(LJM_WEB)}</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>"""
