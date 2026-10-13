"""Inline (CID) image assets that ship inside the outbound message.

Why CID instead of a hosted URL: a hosted image needs a public host that stays
up (the old Vercel host was deleted and every banner 404'd). An inline
attachment travels with the mail, so nothing external has to exist.

The renderer references ``cid:<name>``; ``sender._build_mime`` scans the html
for those references and attaches the matching file. Both sides import the
names from here so they cannot drift. The files live under ``backend/`` so both
the ``api`` and ``worker`` images (build context ``./backend``) carry them.
"""

from __future__ import annotations

from pathlib import Path

ASSETS_DIR = Path(__file__).resolve().parent / "assets"

CID_TRUCK_BANNER = "truck-banner"
CID_LOGO = "ljm-logo"

# name -> (file path, maintype, subtype)
INLINE_ASSETS: dict[str, tuple[Path, str, str]] = {
    CID_TRUCK_BANNER: (ASSETS_DIR / "truck-banner@2x.png", "image", "png"),
    CID_LOGO: (ASSETS_DIR / "ljm-logo.png", "image", "png"),
}
