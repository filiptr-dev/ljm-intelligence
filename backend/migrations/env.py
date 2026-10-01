"""Alembic env — async engine, URL from app settings.

Tests can pass a URL via `config.attributes["database_url"]` instead of touching real settings.
"""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection, make_url
from sqlalchemy.ext.asyncio import create_async_engine

import app.models  # noqa: F401 — registers tables on Base.metadata
from app.config import get_settings, psycopg_url
from app.db import Base

config = context.config
if config.config_file_name is not None and config.attributes.get("configure_logger", True):
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _database_url() -> str:
    url = config.attributes.get("database_url")
    if isinstance(url, str):
        return psycopg_url(url)
    # Prefer the direct (non-pooled) URL for migrations — Alembic doesn't
    # share connections with the app; the pooler doesn't help and can
    # cause issues with DDL across statements. Falls back to the pooled
    # URL if direct isn't configured (dev/test with one local PG).
    s = get_settings()
    return s.database_url_direct or s.database_url


def run_migrations_offline() -> None:
    context.configure(url=_database_url(), target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = _database_url()
    # Mirror app.db.create_engine: psycopg-only connect_args must not be passed
    # to aiosqlite (which rejects `prepare_threshold` / `connect_timeout`), so
    # only apply them for a Postgres backend. Postgres path unchanged.
    if make_url(url).get_backend_name() == "sqlite":
        engine = create_async_engine(url)
    else:
        engine = create_async_engine(url, connect_args={"prepare_threshold": None, "connect_timeout": 15})
    try:
        async with engine.connect() as connection:
            await connection.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
