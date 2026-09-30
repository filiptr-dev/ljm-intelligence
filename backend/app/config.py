"""Settings — env-driven, validated once at startup so a bad deploy fails fast.

Neon note: the pooled URL comes as `postgresql://…?sslmode=require&channel_binding=require`.
SQLAlchemy needs the driver named; psycopg3 accepts `sslmode` and `channel_binding` verbatim,
so we only rewrite the scheme and leave query params intact. Keeping security parameters is the
whole point of the exercise — never strip `sslmode`/`channel_binding` to make a driver happy.
"""

from functools import lru_cache
from typing import Annotated, Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


def psycopg_url(url: str) -> str:
    """`postgres://` / `postgresql://` → `postgresql+psycopg://`. Query string preserved."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url.removeprefix(prefix)
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", frozen=True)

    app_env: Literal["development", "test", "production"] = "development"
    log_level: Literal["debug", "info", "warning", "error"] = "info"

    # Neon pooled URL (…-pooler.<region>.aws.neon.tech, sslmode=require, channel_binding=require).
    database_url: str
    db_pool_size: int = Field(default=5, ge=1, le=20)
    # First query after Neon autosuspend takes ~1–2s. Keep the health timeout generous.
    db_health_timeout: float = Field(default=8.0, gt=0)

    # Vercel origin(s). Comma-separated in env.
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["http://localhost:3000"])
    cors_allowed_origin_regex: str | None = None

    # Gemini — used from Slice 3 on. Present here so config is complete.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    # FMCSA paginator knobs — see plan `2026-09-30-fmcsa-crawl-depth`.
    # `fmcsa_app_token` lifts us from the shared SODA throttle bucket to a per-app one;
    # optional (the paginator works keyless). `fmcsa_page_size` is the SODA `$limit`.
    # `fmcsa_per_run_page_cap` bounds a *steady-state* run (~ page_cap × page_size rows).
    # `fmcsa_backfill_page_cap` is used only on the first run (frontier is NULL) and lets
    # us pull a comfortable year of B/S/F registrations in one shot.
    # `fmcsa_time_budget_s` is a soft wall-clock cap — the paginator finishes the
    # current page then stops and records `stopped_reason="timeout"` so partial
    # progress lands even when Render reclaims the free-tier dyno.
    fmcsa_app_token: SecretStr | None = None
    fmcsa_page_size: int = Field(default=500, ge=1, le=50000)
    fmcsa_per_run_page_cap: int = Field(default=6, ge=1, le=200)
    fmcsa_backfill_page_cap: int = Field(default=40, ge=1, le=500)
    fmcsa_time_budget_s: float = Field(default=120.0, gt=0)

    # Cron shared secret — used from Slice 2 on.
    cron_secret: SecretStr | None = None

    # Demo safety net. Real outreach requires BOTH this=false AND settings.auto_send_enabled=true.
    simulated_delivery: bool = True

    frontend_origin: str | None = None

    # Shipper Finder — OSM Overpass ingest (Slice 2b). Enabled by default; the
    # source itself fails gracefully so a bad Overpass day never blocks the
    # FMCSA pass. `osm_overpass_max_states` bounds how many in-region states
    # each run touches (0 = all 32); the demo cron sets a low value to keep
    # runs short. `osm_overpass_states` optionally overrides the state list
    # (comma-separated 2-letter codes) — useful for a fast smoke run.
    osm_overpass_enabled: bool = True
    osm_overpass_max_states: int = Field(default=0, ge=0, le=64)
    osm_overpass_states: Annotated[list[str], NoDecode] = Field(default_factory=list)

    @field_validator("osm_overpass_states", mode="before")
    @classmethod
    def _split_osm_states(cls, value: object) -> object:
        if isinstance(value, str):
            return [s.strip().upper() for s in value.split(",") if s.strip()]
        return value

    @field_validator("database_url")
    @classmethod
    def _use_psycopg(cls, url: str) -> str:
        return psycopg_url(url)

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _split_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [o.strip().rstrip("/") for o in value.split(",") if o.strip()]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
