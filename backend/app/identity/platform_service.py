"""Identity platform-settings service — the singleton `settings` row.

The ``settings`` row is platform-level (one row, id=1) until
``platform_settings`` + ``tenant_settings`` fully supersede it per the
foundation plan. This service holds the get/upsert logic that the thin
``app/api/settings.py`` router delegates to.

AI-features merging and the arm-time guard around auto-outreach stay here
so the router is pure HTTP: parse, call, serialise.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import Settings
from app.integrations.adapters.ai.provider import ALLOWED_MODELS, DEFAULT_FEATURES, FEATURE_NAMES
from app.models import SettingsRow
from app.services.unsub_config import effective_unsub, unsub_missing_field


@dataclass
class SettingsSnapshot:
    row: SettingsRow
    unsub_secret_set: bool
    unsub_config_ready: bool
    ai_features: dict


def effective_ai_features(stored: dict | None) -> dict:
    """Merge stored overrides over DEFAULT_FEATURES; invalid entries fall through."""
    out: dict[str, dict[str, str]] = {}
    stored = stored or {}
    for feature, default in DEFAULT_FEATURES.items():
        choice = stored.get(feature) if isinstance(stored.get(feature), dict) else None
        provider = (choice or {}).get("provider") or default["provider"]
        model = (choice or {}).get("model") or default["model"]
        if provider not in ALLOWED_MODELS or model not in ALLOWED_MODELS.get(provider, []):
            provider, model = default["provider"], default["model"]
        out[feature] = {"provider": provider, "model": model}
    return out


def validate_ai_features(value: dict) -> dict:
    """Validator the router's pydantic model reuses. Raises ValueError on bad input."""
    if not isinstance(value, dict):
        raise ValueError("ai_features must be an object")
    out: dict[str, dict[str, str]] = {}
    for feature, choice in value.items():
        if feature not in FEATURE_NAMES:
            raise ValueError(f"unknown ai feature: {feature}")
        if not isinstance(choice, dict):
            raise ValueError(f"ai_features[{feature}] must be an object")
        provider = str(choice.get("provider") or "")
        model = str(choice.get("model") or "")
        if provider not in ALLOWED_MODELS:
            raise ValueError(f"ai_features[{feature}].provider invalid: {provider}")
        if model not in ALLOWED_MODELS[provider]:
            raise ValueError(f"ai_features[{feature}].model not allowed for {provider}: {model}")
        out[feature] = {"provider": provider, "model": model}
    return out


class UnsubMissingError(Exception):
    """Raised when auto-outreach cannot be armed because the unsub link is unbuildable.

    The HTTP layer surfaces this as a 409 with the missing-field details.
    """

    def __init__(self, missing: str) -> None:
        self.missing = missing
        super().__init__(missing)


async def _get_or_create(session: AsyncSession) -> SettingsRow:
    row = (
        await session.execute(select(SettingsRow).where(SettingsRow.id == 1))
    ).scalar_one_or_none()
    if not row:
        row = SettingsRow(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


def _snapshot(settings: Settings, row: SettingsRow) -> SettingsSnapshot:
    secret, base_url = effective_unsub(settings, row)
    return SettingsSnapshot(
        row=row,
        unsub_secret_set=bool(secret),
        unsub_config_ready=bool(secret and base_url),
        ai_features=effective_ai_features(row.ai_features),
    )


async def get_settings(session: AsyncSession, settings: Settings) -> SettingsSnapshot:
    return _snapshot(settings, await _get_or_create(session))


async def put_settings(
    session: AsyncSession, settings: Settings, patch: dict[str, Any]
) -> SettingsSnapshot:
    """Apply the patch and return the fresh snapshot.

    Arm-time guard: refuses (via ``UnsubMissingError``) to arm auto-outreach
    when the unsubscribe link can't be built. Disarming is never blocked.
    """
    row = await _get_or_create(session)
    if patch.get("auto_outreach_enabled") is True:
        missing = unsub_missing_field(settings, row)
        if missing:
            raise UnsubMissingError(missing)
    for k, v in patch.items():
        setattr(row, k, v)
    row.updated_at = datetime.now(UTC).replace(tzinfo=None)
    await session.commit()
    await session.refresh(row)
    return _snapshot(settings, row)
