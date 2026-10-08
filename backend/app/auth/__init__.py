"""Compatibility shim — re-exports from `app.identity.auth`.

The auth code moved into `app.identity.auth.*` as part of the 2026-10-08
onion/SOLID refactor. This shim keeps the old import paths working so
callers that still write `from app.auth.deps import current_user` do not
break. Do NOT add new symbols here — add them to `app.identity.auth.*`
and let this file remain a thin re-export.
"""

from app.identity.auth import *  # noqa: F401,F403
