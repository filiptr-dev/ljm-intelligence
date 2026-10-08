"""HS256 JWT mint + decode. Stateless — logout clears the Vercel cookie only.

Signing secret: ``settings.auth_jwt_secret_override`` (env) > durable
``settings`` row column backfilled by migration 0008. Never falls through
to a random value — a missing secret raises so a mis-deployed instance
fails loudly instead of silently signing with ``""``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.models import SettingsRow

ALGORITHM = "HS256"


class AuthConfigError(RuntimeError):
    """Raised when no JWT secret is available anywhere — hard-fail, don't guess."""


@dataclass(frozen=True)
class TokenClaims:
    sub: str  # user.id (ULID)
    email: str
    role: str


# Per-process cache for the JWT secret. The secret is immutable at steady
# state (set in `settings` row at migration 0008); on rotation, roll the pod.
# Caching avoids a SELECT on every authenticated request.
_jwt_secret_cache: str | None = None


async def effective_auth_jwt_secret(settings: Settings, session: AsyncSession) -> str:
    """Env override wins; otherwise the migration-seeded settings row.

    Cached per process — a rotation requires a redeploy. Pattern copies
    :func:`app.services.unsub_config.effective_unsub` so the "env > DB"
    precedence is uniform across the two durable-secret knobs.
    """
    global _jwt_secret_cache
    if _jwt_secret_cache is not None:
        return _jwt_secret_cache
    env = getattr(settings, "auth_jwt_secret_override", None)
    if env is not None:
        _jwt_secret_cache = env.get_secret_value()
        return _jwt_secret_cache
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if row and row.auth_jwt_secret:
        _jwt_secret_cache = row.auth_jwt_secret
        return _jwt_secret_cache
    raise AuthConfigError(
        "auth_jwt_secret is unset — migration 0008 should have backfilled settings.auth_jwt_secret; "
        "set AUTH_JWT_SECRET env var to override."
    )


def reset_jwt_secret_cache() -> None:
    """Test hook — reset the process-local JWT secret cache."""
    global _jwt_secret_cache
    _jwt_secret_cache = None


def mint_access_token(
    *,
    secret: str,
    user_id: str,
    email: str,
    role: str,
    ttl_days: int,
) -> tuple[str, int]:
    """Return ``(token, expires_in_seconds)``. ``iat``/``exp`` baked in."""
    now = datetime.now(UTC)
    exp = now + timedelta(days=ttl_days)
    payload = {
        "sub": user_id,
        "email": email,
        "role": role,
        "iat": int(now.timestamp()),
        "exp": int(exp.timestamp()),
    }
    token = jwt.encode(payload, secret, algorithm=ALGORITHM)
    return token, int((exp - now).total_seconds())


def decode_access_token(token: str, secret: str) -> TokenClaims:
    """Verify signature + exp. Raises :class:`jwt.InvalidTokenError` on any failure."""
    payload = jwt.decode(token, secret, algorithms=[ALGORITHM])
    return TokenClaims(sub=payload["sub"], email=payload["email"], role=payload["role"])
