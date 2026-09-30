"""Dump the FastAPI OpenAPI schema to stdout — no server, no network, no Neon.

Why this exists
---------------
The frontend uses `openapi-typescript` to generate `src/lib/api/schema.d.ts` from
the FastAPI OpenAPI. We refuse to point the generator at the live backend when
`backend/.env` is loaded with the PRODUCTION Neon URL — one accidental startup
against prod is one too many. Instead: build the app in-process against an
in-memory SQLite (no engine ever connects) and dump `app.openapi()`.

Usage
-----
    cd backend && \
        DATABASE_URL=sqlite+aiosqlite:///:memory: APP_ENV=test \
        uv run python -m scripts.dump_openapi > /tmp/openapi.json

The frontend's `pnpm gen:api` script wraps this so a contributor never has to
remember the env pin (see frontend/package.json).

Safety
------
- ``DATABASE_URL`` is force-pinned to sqlite+aiosqlite in-memory even if the
  caller forgot the env prefix; ``backend/.env`` is loaded with ``override=False``
  so it can never clobber us. Same belt-and-suspenders as ``tests/conftest.py``.
- ``create_app()`` never opens a DB connection; the engine is created inside
  the lifespan context, which we never enter.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv

_SAFE_DB_URL = "sqlite+aiosqlite:///:memory:"
os.environ["DATABASE_URL"] = _SAFE_DB_URL
load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)
os.environ["APP_ENV"] = "test"

from app.config import Settings
from app.main import create_app

_settings = Settings()
_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1"}
if not (
    _settings.database_url.startswith("sqlite") or (urlparse(_settings.database_url).hostname or "") in _LOCAL_HOSTS
):
    print(
        f"refusing to dump openapi against non-local DATABASE_URL={_settings.database_url}",
        file=sys.stderr,
    )
    sys.exit(2)


def main() -> None:
    app = create_app(_settings)
    schema = app.openapi()
    json.dump(schema, sys.stdout, indent=2, sort_keys=False)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
