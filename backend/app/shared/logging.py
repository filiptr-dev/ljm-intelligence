"""Structured JSON logging + request_id middleware.

Keeps the standard `logging` surface so existing `log.info("…", extra={…})`
calls keep working. The formatter serialises each record as a single JSON
line with:
  * `ts`, `level`, `logger`, `message`
  * `request_id` / `job_id` / `tenant_id` from a contextvar, when bound.
  * `extra` fields the caller passed in via `logger.info(…, extra={…})`.

This is deliberately minimal — structlog is a dep away but swapping JSON
serialisation is enough to make Render / Sentry / Logtail happy without
pulling a new library into the app.
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from contextvars import ContextVar

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

_request_id_ctx: ContextVar[str | None] = ContextVar("ljm_request_id", default=None)
_job_id_ctx: ContextVar[str | None] = ContextVar("ljm_job_id", default=None)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        rid = _request_id_ctx.get()
        if rid:
            payload["request_id"] = rid
        jid = _job_id_ctx.get()
        if jid:
            payload["job_id"] = jid
        # Pull tenant from the shared contextvar if bound.
        try:
            from app.shared.tenant import _tenant_ctx

            tid = _tenant_ctx.get()
            if tid:
                payload["tenant_id"] = tid
        except Exception:  # noqa: BLE001, S110 - a formatter must never raise or log recursively
            pass
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Caller-supplied `extra` fields.
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "levelname", "levelno", "pathname", "filename",
                     "module", "exc_info", "exc_text", "stack_info", "lineno",
                     "funcName", "created", "msecs", "relativeCreated", "thread",
                     "threadName", "processName", "process", "name", "message",
                     "taskName"):
                continue
            try:
                json.dumps(v)
                payload[k] = v
            except TypeError:
                payload[k] = repr(v)
        return json.dumps(payload)


def configure_json_logging(level: str = "info") -> None:
    """Install the JSON formatter on the root logger. Idempotent."""
    root = logging.getLogger()
    root.setLevel(level.upper())
    # Replace existing handlers so each boot has one JSON handler, not two.
    for h in list(root.handlers):
        root.removeHandler(h)
    h = logging.StreamHandler()
    h.setFormatter(JsonFormatter())
    root.addHandler(h)


class RequestIdMiddleware(BaseHTTPMiddleware):
    """Bind a request_id into the contextvar for the lifetime of the request.

    Picks up an inbound `X-Request-Id` if present (so a reverse proxy's id
    flows through); otherwise mints a short uuid4.
    """
    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next):
        inbound = request.headers.get("x-request-id")
        rid = inbound or uuid.uuid4().hex[:16]
        token = _request_id_ctx.set(rid)
        try:
            response = await call_next(request)
            response.headers["x-request-id"] = rid
            return response
        finally:
            _request_id_ctx.reset(token)


def set_job_id(job_id: str) -> None:
    """Called by the (not-yet-wired) queue worker to bind the current job id."""
    _job_id_ctx.set(job_id)
