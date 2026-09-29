"""Production ASGI entry point: the platform app plus observability.

    uvicorn dip.api.asgi:app --workers 4        # what ``scripts/dmis.py serve`` runs

Wraps ``dip.api.app:app`` (outermost) with the ``X-Request-ID`` middleware and configures logging
(``DIP_LOG_FORMAT=json``) and optional Sentry (``SENTRY_DSN``). See ``dip.logging_setup``.
The FastAPI instance itself stays available as ``fastapi_app``.
"""

from __future__ import annotations

from dip.api.app import app as fastapi_app
from dip.logging_setup import RequestIdMiddleware, configure_logging, init_sentry

configure_logging()
init_sentry()
app = RequestIdMiddleware(fastapi_app)

__all__ = ["app", "fastapi_app"]
