"""Owner Settings — singleton row.

`GET /settings` upserts a default row on first read so the frontend is never blank.
`PUT /settings` patches any subset of fields.

The unsubscribe **secret** is deliberately not patchable through the public API
— the migration seeds a durable value; the API only exposes an advisory
`unsub_secret_set` boolean so the UI can show "set" / "not set" without ever
leaking the token. The unsubscribe **base URL** is user-editable, https-only.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, Request
from pydantic import AnyHttpUrl, BaseModel, Field, TypeAdapter, ValidationError, field_validator
from sqlalchemy import select

from app.models import SettingsRow
from app.services.unsub_config import effective_unsub, unsub_missing_field
from app.sources.provider import ALLOWED_MODELS, DEFAULT_FEATURES, FEATURE_NAMES

router = APIRouter(prefix="/settings", tags=["settings"])


@dataclass
class _PendingUnsubState:
    """Shape-compatible stand-in for ``SettingsRow`` for the arm-time guard.

    ``effective_unsub`` / ``unsub_missing_field`` only read these two attrs,
    so a tiny dataclass is a cleaner contract than an ad-hoc inner class — if
    the config helpers ever grow a new attribute, the type system flags it
    here instead of surprising us with a runtime ``AttributeError``.
    """

    unsubscribe_secret: str | None
    unsubscribe_base_url: str | None


_HTTPS_URL_ADAPTER = TypeAdapter(AnyHttpUrl)


class SettingsOut(BaseModel):
    threshold: int
    auto_send_enabled: bool
    auto_send_template_id: str | None
    tone: str
    daily_send_cap: int
    updated_at: str
    # Enrichment scope (2026-09-30) — auto-outreach + fit-weight overrides.
    auto_outreach_enabled: bool
    auto_outreach_template_id: str | None
    auto_outreach_daily_cap: int
    auto_outreach_window_start_h: int
    auto_outreach_window_end_h: int
    auto_outreach_status_filter: str
    auto_outreach_min_fit: int
    fit_weights: dict | None
    # Unsubscribe config (migration 0007). The secret VALUE is never returned;
    # only a boolean flag so the UI can show set/not-set. `unsub_config_ready`
    # gates the auto-outreach toggle client-side.
    unsubscribe_base_url: str | None
    unsub_secret_set: bool
    unsub_config_ready: bool
    # AI provider matrix (migration 0009). Each feature → {provider, model}.
    # Missing / unknown keys fall back to code defaults in `provider.DEFAULT_FEATURES`.
    ai_features: dict


class SettingsPatch(BaseModel):
    threshold: int | None = Field(default=None, ge=0, le=100)
    auto_send_enabled: bool | None = None
    auto_send_template_id: str | None = None
    tone: str | None = None
    daily_send_cap: int | None = Field(default=None, ge=1, le=1000)
    auto_outreach_enabled: bool | None = None
    auto_outreach_template_id: str | None = None
    auto_outreach_daily_cap: int | None = Field(default=None, ge=1, le=1000)
    auto_outreach_window_start_h: int | None = Field(default=None, ge=0, le=23)
    auto_outreach_window_end_h: int | None = Field(default=None, ge=0, le=23)
    auto_outreach_status_filter: str | None = None
    auto_outreach_min_fit: int | None = Field(default=None, ge=0, le=100)
    fit_weights: dict | None = None
    # Https-only; the empty string is a legal "clear it" signal.
    unsubscribe_base_url: str | None = None
    # AI provider matrix. Each entry MUST be ``{provider: "gemini"|"claude",
    # model: <allowed-model>}``. Validator rejects unknown features / models /
    # providers with 422 so the UI can surface a specific message.
    ai_features: dict | None = None

    @field_validator("unsubscribe_base_url")
    @classmethod
    def _https_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value == "":
            return ""
        try:
            parsed = _HTTPS_URL_ADAPTER.validate_python(value)
        except ValidationError as exc:
            raise ValueError("unsubscribe_base_url must be a valid URL") from exc
        if parsed.scheme != "https":
            raise ValueError("unsubscribe_base_url must use https")
        return str(parsed).rstrip("/")

    @field_validator("ai_features")
    @classmethod
    def _validate_ai_features(cls, value: dict | None) -> dict | None:
        if value is None:
            return None
        if not isinstance(value, dict):
            raise ValueError("ai_features must be an object")  # noqa: TRY004
        out: dict[str, dict[str, str]] = {}
        for feature, choice in value.items():
            if feature not in FEATURE_NAMES:
                raise ValueError(f"unknown ai feature: {feature}")
            if not isinstance(choice, dict):
                raise ValueError(f"ai_features[{feature}] must be an object")  # noqa: TRY004
            provider = str(choice.get("provider") or "")
            model = str(choice.get("model") or "")
            if provider not in ALLOWED_MODELS:
                raise ValueError(f"ai_features[{feature}].provider invalid: {provider}")
            if model not in ALLOWED_MODELS[provider]:
                raise ValueError(f"ai_features[{feature}].model not allowed for {provider}: {model}")
            out[feature] = {"provider": provider, "model": model}
        return out


def _serialize(request: Request, row: SettingsRow) -> SettingsOut:
    settings = request.app.state.settings
    secret, base_url = effective_unsub(settings, row)
    return SettingsOut(
        threshold=row.threshold,
        auto_send_enabled=row.auto_send_enabled,
        auto_send_template_id=row.auto_send_template_id,
        tone=row.tone,
        daily_send_cap=row.daily_send_cap,
        updated_at=row.updated_at.isoformat() if row.updated_at else "",
        auto_outreach_enabled=row.auto_outreach_enabled,
        auto_outreach_template_id=row.auto_outreach_template_id,
        auto_outreach_daily_cap=row.auto_outreach_daily_cap,
        auto_outreach_window_start_h=row.auto_outreach_window_start_h,
        auto_outreach_window_end_h=row.auto_outreach_window_end_h,
        auto_outreach_status_filter=row.auto_outreach_status_filter,
        auto_outreach_min_fit=row.auto_outreach_min_fit,
        fit_weights=row.fit_weights,
        # Advisory only. The base URL is echoed so the form prefills; the
        # secret is *never* in the response — only its set/not-set bit.
        unsubscribe_base_url=row.unsubscribe_base_url,
        unsub_secret_set=bool(secret),
        unsub_config_ready=bool(secret and base_url),
        ai_features=_effective_ai_features(row.ai_features),
    )


def _effective_ai_features(stored: dict | None) -> dict:
    """Merge the stored overrides over DEFAULT_FEATURES; invalid entries fall through."""
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


async def _get_or_create(session) -> SettingsRow:
    row = (await session.execute(select(SettingsRow).where(SettingsRow.id == 1))).scalar_one_or_none()
    if not row:
        row = SettingsRow(id=1)
        session.add(row)
        await session.commit()
        await session.refresh(row)
    return row


@router.get("", response_model=SettingsOut)
async def get_settings(request: Request) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        return _serialize(request, await _get_or_create(s))


@router.put("", response_model=SettingsOut)
async def put_settings(request: Request, patch: SettingsPatch) -> SettingsOut:
    async with request.app.state.sessionmaker() as s:
        row = await _get_or_create(s)
        data = patch.model_dump(exclude_unset=True)

        # Apply patch to a shadow copy so we can validate the post-write state
        # before committing. Two knobs we care about together:
        #  * `unsubscribe_base_url` change → recompute readiness against
        #    `effective_unsub` (env overrides still apply).
        #  * `auto_outreach_enabled=True` → refuse (409) unless the post-patch
        #    config is ready. Disarming is never blocked.
        # Only guard the *arm* action itself. Disarming is never blocked; a
        # blanked base URL on an already-armed row is caught at send-time via
        # the `no_unsub_config` short-circuit.
        if data.get("auto_outreach_enabled") is True:
            pending = _PendingUnsubState(
                unsubscribe_secret=row.unsubscribe_secret,
                unsubscribe_base_url=data.get("unsubscribe_base_url", row.unsubscribe_base_url),
            )
            missing = unsub_missing_field(request.app.state.settings, pending)
            if missing:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "error": "unsub_config_missing",
                        "missing": missing,
                        "message": "Set the unsubscribe base URL before arming auto-outreach.",
                    },
                )

        for k, v in data.items():
            setattr(row, k, v)
        # DB column is naive-UTC; keep tz-aware `now()` then strip for schema parity.
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        await s.commit()
        await s.refresh(row)
        return _serialize(request, row)
