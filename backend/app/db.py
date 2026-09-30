"""Async SQLAlchemy engine + Base. One place that opens DB connections."""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy import MetaData, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import Settings


class Base(DeclarativeBase):
    """All models subclass this so Alembic autogenerate sees them under one metadata."""

    metadata = MetaData(
        naming_convention={
            "ix": "%(table_name)s_%(column_0_N_name)s",
            "uq": "%(table_name)s_%(column_0_N_name)s_key",
            "ck": "%(table_name)s_%(constraint_name)s_check",
            "fk": "%(table_name)s_%(column_0_N_name)s_fkey",
            "pk": "%(table_name)s_pkey",
        }
    )


def create_engine(settings: Settings) -> AsyncEngine:
    """DB engine. Postgres-tuned for Neon; sqlite fallback for local/tests.

    Postgres branch (Neon / any postgres backend):
    - `pool_pre_ping`: Neon autosuspend + Render idle both drop connections; a ping avoids a stale-conn 500.
    - `prepare_threshold=None`: psycopg3 skips server-side prepared statements, so a pgbouncer transaction
      pooler would also work if we ever switch to it.
    - `connect_timeout=15`: first connect after Neon autosuspend can be slow (cold branch wake).

    Sqlite branch: aiosqlite rejects the psycopg-only `connect_args` above AND
    the pool sizing (StaticPool-style single-conn is what makes an in-memory DB
    usable across sessions). We only take the sqlite branch when the URL says
    so — Postgres behaviour is byte-for-byte the same as before.
    """
    backend = make_url(settings.database_url).get_backend_name()
    if backend == "sqlite":
        # No pool tuning, no psycopg kwargs — just a working async engine.
        return create_async_engine(settings.database_url)
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=0,
        pool_pre_ping=True,
        pool_recycle=300,
        connect_args={"prepare_threshold": None, "connect_timeout": 15},
    )


def create_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    sessionmaker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with sessionmaker() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]


async def ping(engine: AsyncEngine) -> None:
    """Cheap SELECT 1. Retries once — Neon's cold-wake can drop the first attempt."""
    last_exc: Exception | None = None
    for _ in range(2):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("select 1"))
            return
        except Exception as exc:  # noqa: BLE001 — surface the last failure to the caller
            last_exc = exc
    assert last_exc is not None
    raise last_exc
