"""POST /crawl/run must reject requests without the shared cron secret when one is set."""

from unittest.mock import patch

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app


def _mk_settings(**overrides) -> Settings:
    from pydantic import SecretStr

    base = Settings()
    return base.model_copy(
        update={
            "cron_secret": SecretStr("s3cret"),
            "gemini_api_key": None,  # skip the gemini stage in the pipeline
            **overrides,
        }
    )


@pytest.mark.asyncio
async def test_crawl_run_rejects_wrong_secret() -> None:
    app = create_app(_mk_settings())
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            r = await c.post("/crawl/run", headers={"X-Cron-Secret": "nope"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_crawl_run_accepts_correct_secret_and_returns_202() -> None:
    app = create_app(_mk_settings())
    # No FMCSA call, no Gemini — just prove the auth + 202 path.
    async def fake_fetch(**_):  # noqa: ANN001
        return []
    with patch("app.pipeline.run.fetch_fmcsa", side_effect=fake_fetch):
        async with app.router.lifespan_context(app):
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://test") as c:
                r = await c.post(
                    "/crawl/run?limit=5",
                    headers={"X-Cron-Secret": "s3cret"},
                )
    assert r.status_code == 202
    body = r.json()
    assert body["status"] == "queued"
    assert body["run_id"].startswith("run_")
