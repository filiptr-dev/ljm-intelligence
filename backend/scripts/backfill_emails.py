"""One-off: fill `leads.primary_email` from FMCSA `email_address` for leads that have a DOT.

FMCSA + Neon only. Never touches Gemini, never deletes, only fills NULL emails
(an email already on a lead is kept). Safe to re-run.

    cd backend && uv run python -m scripts.backfill_emails [--dry-run]
"""

from __future__ import annotations

import asyncio
import sys

import httpx
from sqlalchemy import select, update

from app.config import Settings
from app.db import create_engine
from app.models import Lead
from app.sources.emails import add_contact_email, normalize_email
from app.sources.fmcsa import SODA_URL

BATCH = 50


async def fetch_emails(dots: list[str]) -> dict[str, str]:
    """dot_number -> normalised email, for the DOTs FMCSA has an address for."""
    quoted = ",".join(f"'{d}'" for d in dots if d.isdigit())
    params = {
        "$select": "dot_number,email_address",
        "$where": f"dot_number in({quoted}) AND email_address IS NOT NULL",
        "$limit": str(len(dots) * 2),
    }
    headers = {"User-Agent": "LJM-Intelligence-Bot/1.0 (+https://ljminternational.com)"}
    async with httpx.AsyncClient(timeout=30.0) as client:
        resp = await client.get(SODA_URL, params=params, headers=headers)
        resp.raise_for_status()
    out: dict[str, str] = {}
    for row in resp.json():
        email = normalize_email(row.get("email_address"))
        if email and row.get("dot_number"):
            out[str(row["dot_number"]).strip()] = email
    return out


async def main(dry_run: bool) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    engine = create_engine(Settings())
    sm = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with sm() as s:
            rows = (
                await s.execute(
                    select(Lead.id, Lead.dot, Lead.phone).where(Lead.primary_email.is_(None), Lead.dot.is_not(None))
                )
            ).all()
        print(f"leads missing an email with a DOT: {len(rows)}")
        filled = 0
        for i in range(0, len(rows), BATCH):
            chunk = rows[i : i + BATCH]
            found = await fetch_emails([r.dot for r in chunk])
            async with sm() as s:
                for r in chunk:
                    email = found.get(r.dot)
                    if not email:
                        continue
                    filled += 1
                    if dry_run:
                        continue
                    await s.execute(
                        update(Lead).where(Lead.id == r.id, Lead.primary_email.is_(None)).values(primary_email=email)
                    )
                    await add_contact_email(s, lead_id=r.id, email=email, phone=r.phone, source="FMCSA Census")
                if not dry_run:
                    await s.commit()
            print(f"  batch {i // BATCH + 1}: {len(found)} emails from FMCSA")
        print(f"{'would fill' if dry_run else 'filled'}: {filled}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main(dry_run="--dry-run" in sys.argv))
