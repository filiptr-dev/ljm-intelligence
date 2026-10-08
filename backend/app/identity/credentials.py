"""CredentialVault — the only path that reads/writes `tenant_credentials`.

AES-GCM per record. Payload layout on disk:
    secret_enc = nonce (12 bytes) || ciphertext || tag (16 bytes)

Key material resolution (first match wins, same pattern as
``effective_auth_jwt_secret``):

    1. ``TENANT_CRED_KEY`` env — a 32-byte base64 blob; wins when set.
    2. ``settings.cred_key`` DB column — backfilled by migration 0025.
    3. First-use bootstrap — generate 32 random bytes, persist to the
       settings row, return. Means a deploy with *no* env still has a
       working vault; the key is durable from the moment anything writes
       the first credential.

We never log plaintext, never surface the raw key, and the key cache is
per-process (reset hook exists for tests).
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.tenant import TenantId


class VaultConfigError(RuntimeError):
    pass


class VaultNotFound(KeyError):
    pass


_key_cache: bytes | None = None
_key_lock = asyncio.Lock()


def _decode_key(raw: str) -> bytes:
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise VaultConfigError(f"vault key must decode to 32 bytes, got {len(key)}.")
    return key


def _env_key() -> bytes | None:
    raw = os.environ.get("TENANT_CRED_KEY")
    if not raw:
        return None
    return _decode_key(raw)


async def effective_vault_key(session: AsyncSession) -> bytes:
    """Env > DB > bootstrap. Cached per process.

    Writes a new key into ``settings.cred_key`` on first use when both
    env and DB are empty — matches the auth_jwt_secret bootstrap pattern
    (hq rule: a missing durable secret is a cold-start concern, not a
    permanent misconfiguration).
    """
    global _key_cache
    if _key_cache is not None:
        return _key_cache
    async with _key_lock:
        if _key_cache is not None:
            return _key_cache
        env = _env_key()
        if env is not None:
            _key_cache = env
            return _key_cache
        # Lazy import to avoid a circular dep at module load.
        from app.identity.models import SettingsRow

        row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
        if row is not None and row.cred_key:
            _key_cache = _decode_key(row.cred_key)
            return _key_cache
        # Bootstrap — generate + persist.
        new = secrets.token_bytes(32)
        encoded = base64.b64encode(new).decode("ascii")
        if row is None:
            row = SettingsRow(id=1)
            session.add(row)
        row.cred_key = encoded
        await session.commit()
        _key_cache = new
        return _key_cache


def reset_vault_key_cache() -> None:
    """Test hook — reset the per-process vault-key cache."""
    global _key_cache
    _key_cache = None


class CredentialVault:
    """Read/write `tenant_credentials` with per-record AES-GCM.

    ``key`` is optional; the recommended path is ``await for_session(session)``
    which resolves env → DB → bootstrap. Direct ``CredentialVault(key=...)``
    construction stays supported for tests and sync code paths. A
    zero-arg construction with no env falls back to the sync bootstrap:
    callers get a clear error that steers them to the async path.
    """

    def __init__(self, key: bytes | None = None) -> None:
        if key is None:
            env = _env_key()
            if env is None:
                raise VaultConfigError(
                    "vault key not available via env; use `CredentialVault.for_session(session)` "
                    "to resolve via DB."
                )
            key = env
        self._aesgcm = AESGCM(key)

    @classmethod
    async def for_session(cls, session: AsyncSession) -> CredentialVault:
        """Resolve the key via env → DB → bootstrap; return a ready vault."""
        key = await effective_vault_key(session)
        return cls(key=key)

    async def put(
        self,
        session: AsyncSession,
        tenant: TenantId,
        connector: str,
        kind: str,
        secret: dict[str, object],
        meta: dict[str, object] | None = None,
    ) -> None:
        """Encrypt + upsert a credential bundle for `(tenant, connector, kind)`."""
        nonce = secrets.token_bytes(12)
        plaintext = json.dumps(secret, separators=(",", ":")).encode("utf-8")
        ciphertext = self._aesgcm.encrypt(nonce, plaintext, None)
        enc = nonce + ciphertext  # tag is appended by AESGCM
        await session.execute(
            text(
                "INSERT INTO tenant_credentials (id, tenant_id, connector, kind, secret_enc, meta) "
                "VALUES (:id, :tid, :conn, :kind, :enc, CAST(:meta AS JSON)) "
                "ON CONFLICT (tenant_id, connector, kind) DO UPDATE SET "
                "  secret_enc = EXCLUDED.secret_enc, meta = EXCLUDED.meta, rotated_at = now()"
            ),
            {
                "id": secrets.token_hex(13),
                "tid": str(tenant),
                "conn": connector,
                "kind": kind,
                "enc": enc,
                "meta": json.dumps(meta or {}),
            },
        )

    async def get(
        self,
        session: AsyncSession,
        tenant: TenantId,
        connector: str,
        kind: str,
    ) -> dict[str, object]:
        """Decrypt and return the bundle. Raises VaultNotFound if missing."""
        row = await session.execute(
            text(
                "SELECT secret_enc FROM tenant_credentials "
                "WHERE tenant_id = :tid AND connector = :conn AND kind = :kind"
            ),
            {"tid": str(tenant), "conn": connector, "kind": kind},
        )
        enc = row.scalar_one_or_none()
        if enc is None:
            raise VaultNotFound(f"no credential for {connector}/{kind}")
        nonce, body = enc[:12], enc[12:]
        plaintext = self._aesgcm.decrypt(nonce, body, None)
        return json.loads(plaintext)
