"""CredentialVault — the only path that reads/writes `tenant_credentials`.

AES-GCM per record. Payload layout on disk:
    secret_enc = nonce (12 bytes) || ciphertext || tag (16 bytes)

Key material lives in `TENANT_CRED_KEY` env (32-byte base64). We never
store the key in the DB, never log plaintext, and never return the raw
secret through any other seam — adapters receive a decrypted bundle only
when they ask the vault for it.

v0 — scaffolding + unit test ready. Wiring to the ConnectorRegistry
happens during the adapter rehome.
"""
from __future__ import annotations

import base64
import json
import os
import secrets

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.shared.tenant import TenantId


class VaultConfigError(RuntimeError):
    pass


class VaultNotFound(KeyError):
    pass


def _load_key() -> bytes:
    raw = os.environ.get("TENANT_CRED_KEY")
    if not raw:
        raise VaultConfigError(
            "TENANT_CRED_KEY env not set — needed to decrypt tenant_credentials."
        )
    key = base64.b64decode(raw)
    if len(key) != 32:
        raise VaultConfigError(f"TENANT_CRED_KEY must decode to 32 bytes, got {len(key)}.")
    return key


class CredentialVault:
    """Read/write `tenant_credentials` with per-record AES-GCM."""

    def __init__(self, key: bytes | None = None) -> None:
        self._aesgcm = AESGCM(key or _load_key())

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
