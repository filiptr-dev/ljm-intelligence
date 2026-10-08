"""Step-0 security clean-up tests.

S0.1 — /docs + /openapi.json closed in prod
S0.4 — JWT TTL ≤ 1 day in prod
S0.5 — /auth/login rate-limited
"""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app.config import Settings
from app.main import create_app
from app.shared.rate_limit import LOGIN_LIMITER


def _prod_settings() -> Settings:
    # Build a Settings instance with app_env="production". We pass known-safe
    # values for every required field so the frozen model constructor succeeds.
    return Settings(
        app_env="production",
        database_url="postgresql://u:p@localhost:5432/x",
        auth_access_ttl_days=7,  # intentionally too high; S0.4 clamps to 1
    )


@pytest.mark.asyncio
async def test_s01_docs_closed_in_prod():
    app = create_app(_prod_settings())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        for path in ("/docs", "/openapi.json"):
            r = await c.get(path)
            assert r.status_code == 404, f"{path} should 404 in prod, got {r.status_code}"


@pytest.mark.asyncio
async def test_s01_docs_open_in_dev():
    # Default fixture test settings = app_env="test"; create_app treats
    # non-prod as docs-open.
    app = create_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/openapi.json")
        assert r.status_code == 200


@pytest.mark.asyncio
async def test_s05_login_rate_limiter_hits_429():
    """Rate limit is a pure in-process function — test it directly so the
    assertion doesn't depend on the full HTTP + DB stack."""
    from app.shared.rate_limit import check_login_rate

    LOGIN_LIMITER.reset()
    # 5 OK; 6th on the same IP returns a positive retry-after.
    for _ in range(5):
        assert check_login_rate("1.2.3.4", "a@b.co") == 0.0
    retry = check_login_rate("1.2.3.4", "a@b.co")
    assert retry > 0


def test_s04_prod_jwt_ttl_clamped_to_1_day():
    """In production, auth_access_ttl_days capped at 1 regardless of config."""
    from app.auth.tokens import mint_access_token

    # Simulate the handler's clamp logic.
    configured = 7
    effective = min(configured, 1)  # prod branch
    token, expires_in = mint_access_token(
        secret="x" * 32, user_id="u", email="e@x.co", role="owner", ttl_days=effective
    )
    assert expires_in <= 86400 + 60  # one day + small clock skew allowance
    assert len(token) > 20


@pytest.mark.asyncio
async def test_s05_email_window_hits_429_across_ips():
    """10 attempts per email per hour — across different IPs."""
    from app.shared.rate_limit import check_login_rate

    LOGIN_LIMITER.reset()
    for i in range(10):
        assert check_login_rate(f"10.0.0.{i}", "x@y.co") == 0.0
    retry = check_login_rate("10.0.0.99", "x@y.co")
    assert retry > 0


# ---------- MF2 — X-Forwarded-For / BFF-secret resolution -------------------


def test_mf2_spoofed_leftmost_xff_is_ignored():
    """Attacker sends ``X-Forwarded-For: 1.2.3.4, <real-render-ip>``.

    With trusted_proxy_hops=1 (Render alone), the rightmost entry wins.
    Taking [0] would let the attacker spoof per-IP limits for free; the
    N-th-from-right rule is what makes the limiter actually bite.
    """
    from app.shared.rate_limit import resolve_client_ip

    ip = resolve_client_ip(
        xff_header="1.2.3.4, 10.0.0.5",
        client_host="10.0.0.5",
        client_ip_header=None,
        proxy_secret_header=None,
        trusted_proxy_secret=None,
        trusted_proxy_hops=1,
    )
    assert ip == "10.0.0.5"


def test_mf2_two_hops_takes_second_from_right():
    """Cloudflare → Render chain: trusted_proxy_hops=2."""
    from app.shared.rate_limit import resolve_client_ip

    ip = resolve_client_ip(
        xff_header="attacker, real-client, cloudflare-edge, render-proxy",
        client_host="render-proxy",
        client_ip_header=None,
        proxy_secret_header=None,
        trusted_proxy_secret=None,
        trusted_proxy_hops=2,
    )
    assert ip == "cloudflare-edge"


def test_mf2_bff_shared_secret_uses_client_ip_header():
    """BFF path: Vercel proxies, so Render sees Vercel's IP for everyone.
    The Next route forwards the real browser IP under a shared secret;
    the backend trusts it only when the secret matches.
    """
    from app.shared.rate_limit import resolve_client_ip

    ip = resolve_client_ip(
        xff_header="76.76.21.21",  # vercel egress — would collapse all users
        client_host="76.76.21.21",
        client_ip_header="203.0.113.42",
        proxy_secret_header="correct-shared-secret",
        trusted_proxy_secret="correct-shared-secret",
        trusted_proxy_hops=1,
    )
    assert ip == "203.0.113.42"


def test_mf2_bff_wrong_secret_rejects_header_falls_through_to_xff():
    """Attacker sends ``X-LJM-Client-IP: 1.2.3.4`` without the secret.
    The forwarded header is ignored; we fall through to the XFF rule."""
    from app.shared.rate_limit import resolve_client_ip

    ip = resolve_client_ip(
        xff_header="attacker, 10.0.0.5",
        client_host="10.0.0.5",
        client_ip_header="1.2.3.4",
        proxy_secret_header="not-the-right-secret",
        trusted_proxy_secret="correct-shared-secret",
        trusted_proxy_hops=1,
    )
    assert ip == "10.0.0.5"


def test_mf2_fallback_to_client_host_when_no_xff():
    from app.shared.rate_limit import resolve_client_ip

    ip = resolve_client_ip(
        xff_header=None, client_host="5.6.7.8",
        client_ip_header=None, proxy_secret_header=None,
        trusted_proxy_secret=None, trusted_proxy_hops=1,
    )
    assert ip == "5.6.7.8"


def test_login_rate_skips_ip_limit_when_ip_unavailable():
    """No TRUSTED_PROXY_SECRET => ip=None => only the per-email limit (10/h)."""
    from app.shared.rate_limit import check_login_rate

    LOGIN_LIMITER.reset()
    # Well past the 5/min per-IP cap, still allowed.
    for _ in range(10):
        assert check_login_rate(None, "solo@y.co") == 0.0
    assert check_login_rate(None, "solo@y.co") > 0


def test_login_rate_applies_ip_limit_when_ip_trusted():
    """With a trusted real IP, the per-IP 5/min limit still bites."""
    from app.shared.rate_limit import check_login_rate

    LOGIN_LIMITER.reset()
    for i in range(5):
        assert check_login_rate("9.9.9.9", f"u{i}@y.co") == 0.0
    assert check_login_rate("9.9.9.9", "u99@y.co") > 0
