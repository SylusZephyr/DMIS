"""Logging, request ids and optional error reporting.

* ``DIP_LOG_FORMAT=json`` -- one JSON object per log line (for log shippers); default ``text`` keeps the
  plain format. ``DIP_LOG_LEVEL`` sets the level (default INFO).
* Every HTTP request gets a request id: the caller's ``X-Request-ID`` when it is a safe token, else a new
  one. It is returned in the ``X-Request-ID`` response header and attached to every log record written
  while the request is handled (``request_id``).
* Pipeline code runs inside ``log_context(job_id=..., stage=...)`` so its log records carry the job id and
  the stage name.
* ``SENTRY_DSN`` enables Sentry when the ``sentry-sdk`` package is installed (``pip install -e .[observability]``);
  without the package it is skipped with a warning. ``SENTRY_TRACES_SAMPLE_RATE`` (default 0) and
  ``SENTRY_ENVIRONMENT`` are passed through.
"""

from __future__ import annotations

import contextlib
import contextvars
import json
import logging
import os
import re
import sys
import uuid
from datetime import datetime, timezone
from typing import Any, Iterator

REQUEST_ID_HEADER = "X-Request-ID"
_SAFE_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")

request_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("dip_request_id", default=None)
job_id_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("dip_job_id", default=None)
stage_var: contextvars.ContextVar[str | None] = contextvars.ContextVar("dip_stage", default=None)

_CONTEXT_FIELDS = (("request_id", request_id_var), ("job_id", job_id_var), ("stage", stage_var))
_STANDARD_ATTRS = set(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime"}
_configured = False
_sentry_done = False


@contextlib.contextmanager
def log_context(**values: str | None) -> Iterator[None]:
    """Attach ``request_id`` / ``job_id`` / ``stage`` to every log record written inside the block."""
    tokens = []
    for name, var in _CONTEXT_FIELDS:
        if name in values:
            tokens.append((var, var.set(values[name])))
    try:
        yield
    finally:
        for var, tok in reversed(tokens):
            var.reset(tok)


class ContextFilter(logging.Filter):
    """Copies the current request / job / stage ids onto each record (``None`` when not set)."""

    def filter(self, record: logging.LogRecord) -> bool:
        for name, var in _CONTEXT_FIELDS:
            if getattr(record, name, None) is None:
                setattr(record, name, var.get())
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        out: dict[str, Any] = {
            "ts": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for name, _ in _CONTEXT_FIELDS:
            v = getattr(record, name, None)
            if v is not None:
                out[name] = v
        for k, v in vars(record).items():                 # structured extras: log.info("...", extra={...})
            if k not in _STANDARD_ATTRS and k not in out and not k.startswith("_") and k not in ("request_id", "job_id", "stage"):
                out[k] = v
        if record.exc_info:
            out["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(out, default=str, ensure_ascii=False)


def log_format() -> str:
    return (os.environ.get("DIP_LOG_FORMAT") or "text").strip().lower()


def configure_logging(force: bool = False) -> None:
    """Idempotent. JSON mode replaces the root handlers (uvicorn's loggers propagate to it)."""
    global _configured
    if _configured and not force:
        return
    level = (os.environ.get("DIP_LOG_LEVEL") or "INFO").upper()
    root = logging.getLogger()
    if log_format() == "json":
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(JsonFormatter())
        handler.addFilter(ContextFilter())
        root.handlers[:] = [handler]
        for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
            lg = logging.getLogger(name)
            lg.handlers[:] = []
            lg.propagate = True
    else:
        if not root.handlers:
            logging.basicConfig()
        for h in root.handlers:
            if not any(isinstance(f, ContextFilter) for f in h.filters):
                h.addFilter(ContextFilter())
    root.setLevel(level)
    _configured = True


def init_sentry() -> bool:
    """Enable Sentry when ``SENTRY_DSN`` is set and the package is installed. Returns whether it is on."""
    global _sentry_done
    dsn = os.environ.get("SENTRY_DSN")
    if not dsn or _sentry_done:
        return _sentry_done
    try:
        import sentry_sdk
    except ImportError:
        logging.getLogger("dip.observability").warning("SENTRY_DSN is set but sentry-sdk is not installed; "
                                                       "pip install -e .[observability]")
        return False
    from dip import __version__

    sentry_sdk.init(dsn=dsn, release=f"dmis@{__version__}", environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
                    traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "0") or 0), send_default_pii=False)
    _sentry_done = True
    return True


class RequestIdMiddleware:
    """Pure ASGI middleware: request id in, request id out, request id on every log record."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        incoming = None
        for k, v in scope.get("headers") or []:
            if k.lower() == b"x-request-id":
                incoming = v.decode("latin-1").strip()
                break
        rid = incoming if incoming and _SAFE_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(rid)

        async def send_with_id(message):
            if message["type"] == "http.response.start":
                headers = [(k, v) for k, v in message.get("headers", []) if k.lower() != b"x-request-id"]
                headers.append((b"x-request-id", rid.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            request_id_var.reset(token)


def install(app) -> None:
    """Wire observability into a FastAPI app: logging config, Sentry, request-id middleware (outermost)."""
    configure_logging()
    init_sentry()
    app.add_middleware(RequestIdMiddleware)
