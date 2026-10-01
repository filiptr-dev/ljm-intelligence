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

    # Anthropic (ai-provider-layer-claude). Env-only; `ANTHROPIC_API_KEY` on Render.
    # Missing → every Claude-routed feature returns `status="no_api_key"` through
    # the LLMProvider seam, never a 500. The daily crawl keeps running.
    anthropic_api_key: SecretStr | None = None
    claude_default_model: str = "claude-sonnet-5-5"
    claude_cheaper_model: str = "claude-haiku-4-5-20251001"

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

    # Auth (migration 0008). The signing secret lives on the ``settings`` row
    # — minted once by the migration, same pattern as ``unsubscribe_secret`` in
    # 0007. Env override is optional (``AUTH_JWT_SECRET``) and wins at read
    # time; see ``app.auth.tokens.effective_auth_jwt_secret``. TTL is in days
    # because v1 has no refresh flow — just a long-lived access token cleared
    # by the Vercel BFF cookie on logout.
    auth_jwt_secret_override: SecretStr | None = Field(default=None, alias="AUTH_JWT_SECRET")
    auth_access_ttl_days: int = Field(default=7, ge=1, le=30)

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

    # Enrichment (plan `2026-09-30-llm-scraper`). All quota-safe defaults.
    enrichment_enabled: bool = True
    enrichment_per_run_cap: int = Field(default=10, ge=0, le=200)
    enrichment_page_cap_per_company: int = Field(default=8, ge=1, le=32)
    enrichment_request_timeout_s: float = Field(default=15.0, gt=0)
    enrichment_between_requests_s: float = Field(default=1.5, ge=0)
    enrichment_cache_ttl_hours: int = Field(default=24, ge=1, le=720)
    # Freight-relevant titles — case-insensitive substring test in the extractor + validator.
    enrichment_titles_regex: str = (
        r"logistics|transportation|traffic|warehouse|supply chain|distribution|shipping|"
        r"procurement|buyer|operations|vp\s+ops|director\s+of\s+logistics"
    )
    discovery_industries: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: [
            "retail",
            "food & beverage",
            "manufacturing",
            "building materials",
            "paper & packaging",
            "chemicals",
            "steel/metals",
            "agriculture",
            "automotive parts",
        ]
    )
    discovery_per_run_cap: int = Field(default=8, ge=0, le=50)
    gemini_model_enrichment: str = "gemini-3.5-flash-lite"

    # Auto-outreach (scope change 2026-09-30). CAN-SPAM footer address is mandatory
    # for a run — an empty value refuses to send. Kept env-driven so ops can rotate
    # without a DB write.
    outreach_from_email: str = "safety@ljminternational.com"
    outreach_from_name: str = "LJM International"
    outreach_postal_address: str = ""
    # Public base URL for the unsubscribe route. Rendered into every auto-send email.
    unsubscribe_base_url: str | None = None
    # HMAC-SHA256 secret used to sign per-contact unsubscribe tokens. Without this,
    # tokens cannot be minted and POST /unsubscribe returns 400 — a deploy without
    # the secret cannot mass-unsubscribe by URL enumeration.
    unsubscribe_secret: SecretStr | None = None

    @field_validator("discovery_industries", mode="before")
    @classmethod
    def _split_industries(cls, value: object) -> object:
        if isinstance(value, str):
            return [s.strip() for s in value.split(",") if s.strip()]
        return value

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
