"""FastAPI auth deps.

Two deps:

* :func:`current_user` — require a valid bearer; 401 otherwise. Returned
  :class:`UserPrincipal` carries the three fields any downstream check needs
  (id, email, role). We do not re-query the DB on every call — the JWT itself
  is the proof; a user's deactivation only takes effect at next login. For v1
  (one owner, no deactivation UI) that is the correct trade-off; later, when
  staff + deactivation ship, add a short-TTL ``active`` cache keyed on
  ``sub``.
* :func:`require_user_or_cron` — accept EITHER a valid bearer OR a matching
  ``X-Cron-Secret`` header. Used as the router-level dep on routers that mix
  user-facing routes with the cron-triggered ones (crawl, enrichment). Each
  cron route still re-checks the secret inside its own handler, so this dep
  is additive, never a replacement for that handler check.
"""

from __future__ import annotations

import hmac
from dataclasses import dataclass

import jwt
from fastapi import Header, HTTPException, Request, status

from app.auth.tokens import decode_access_token, effective_auth_jwt_secret


@dataclass(frozen=True)
class UserPrincipal:
    id: str
    email: str
    role: str


_BEARER = "bearer "


def _extract_bearer(authorization: str | None) -> str | None:
    if not authorization:
        return None
    if not authorization.lower().startswith(_BEARER):
        return None
    return authorization[len(_BEARER) :].strip() or None


async def current_user(
    request: Request,
    authorization: str | None = Header(default=None),
) -> UserPrincipal:
    """Require a valid access token; 401 on anything else."""
    token = _extract_bearer(authorization)
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    settings = request.app.state.settings
    sessionmaker = request.app.state.sessionmaker
    async with sessionmaker() as session:
        try:
            secret = await effective_auth_jwt_secret(settings, session)
        except Exception as exc:
            # Configuration error — surface as 500 so a bad deploy is visible.
            raise HTTPException(status_code=500, detail="auth not configured") from exc
    try:
        claims = decode_access_token(token, secret)
    except jwt.InvalidTokenError as exc:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token") from exc
    return UserPrincipal(id=claims.sub, email=claims.email, role=claims.role)


async def require_user_or_cron(
    request: Request,
    authorization: str | None = Header(default=None),
    x_cron_secret: str | None = Header(default=None, alias="X-Cron-Secret"),
) -> UserPrincipal | None:
    """Pass if the request carries a valid cron secret OR a valid bearer token.

    Returns None in the cron case (there's no user), the :class:`UserPrincipal`
    otherwise. Routers mix cron + user routes (``/crawl``, ``/enrichment``),
    so this is applied at mount time and the per-handler ``check_secret`` call
    inside the cron routes remains the authoritative guard for THOSE routes.
    """
    settings = request.app.state.settings
    expected = settings.cron_secret.get_secret_value() if settings.cron_secret else None
    if expected and x_cron_secret and hmac.compare_digest(x_cron_secret, expected):
        return None
    return await current_user(request, authorization)
