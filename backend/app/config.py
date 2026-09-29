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
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )
    cors_allowed_origin_regex: str | None = None

    # Gemini — used from Slice 3 on. Present here so config is complete.
    gemini_api_key: SecretStr | None = None
    gemini_model: str = "gemini-3.5-flash-lite"

    # Cron shared secret — used from Slice 2 on.
    cron_secret: SecretStr | None = None

    # Demo safety net. Real outreach requires BOTH this=false AND settings.auto_send_enabled=true.
    simulated_delivery: bool = True

    frontend_origin: str | None = None

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
