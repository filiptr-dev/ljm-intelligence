"""PG16-only: partial unique index raises IntegrityError on duplicate insert.

Skipped under sqlite — the partial unique index exists on sqlite too (via
``CREATE UNIQUE INDEX ... WHERE``), so this covers both paths opportunistically.
"""

from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    os.environ.get("TEST_HARNESS") != "pg16" and not os.environ.get("DATABASE_URL_TEST_PG"),
    reason="requires PG16 harness",
)


@pytest.mark.asyncio
async def test_partial_unique_index_blocks_duplicate_email_norm() -> None:
    from app.main import create_app
    from app.prospecting.models import Lead, LeadContact

    app = create_app()
    async with app.router.lifespan_context(app):
        sm = app.state.sessionmaker
        async with sm() as s:
            s.add(Lead(id="MC-99", name="co", kind="Shipper", state="NJ"))
            await s.flush()
            s.add(LeadContact(
                lead_id="MC-99", name="A", email="x@y.com",
                email_norm="x@y.com", source="site-scrape",
            ))
            await s.commit()

        # Second insert with the same (tenant_id, lead_id, email_norm) must
        # trip the partial unique index.
        from sqlalchemy.exc import IntegrityError

        with pytest.raises(IntegrityError):
            async with sm() as s:
                s.add(LeadContact(
                    lead_id="MC-99", name="A2", email="x@y.com",
                    email_norm="x@y.com", source="gemini-search",
                ))
                await s.commit()
