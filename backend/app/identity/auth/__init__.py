"""Auth package — tiny, single-provider login for v1.

See the plan at
``projects/ljm-intelligence/plan/2026-09-30-simple-password-login.md`` and
amendments 1–3; the shape below is "simplest form still-secure":

* ``passwords`` — argon2id hash / verify (constant time).
* ``tokens`` — HS256 JWT mint + decode. Signing secret resolved via
  :func:`effective_auth_jwt_secret` (env override > ``settings.auth_jwt_secret``
  row).
* ``deps`` — FastAPI deps: ``current_user`` + ``require_user_or_cron`` (so
  the X-Cron-Secret-protected ``/crawl/run`` + ``/enrichment/auto-send``
  can still be reached by GitHub Actions without a bearer token).
"""
