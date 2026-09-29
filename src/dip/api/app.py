"""Intelligence Platform v2 API.

    uvicorn dip.api.app:app --port 8000

``/api/v2/*`` is the platform API used by the Next.js command center.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from dip import __version__
from dip.api.routes import auth as auth_routes
from dip import connectors  # noqa: F401 -- registers the dataset.available handler
from dip.api.routes import catalog, enterprise, intelligence, metrics, operations, orgs, people, pilot, workflow
from dip.api.routes import acquire, exports, keywords, knowledge, own_sales, sourcing_intel, watch
from dip.api.security import BodySizeLimitMiddleware, RateLimitMiddleware, SecurityHeadersMiddleware
from dip.audit import AuditMiddleware
from dip.auth import auth_enabled
from dip.settings import get_settings

logging.basicConfig(level=logging.INFO)
logging.getLogger("dip.api").info("authentication %s", "ENFORCED" if auth_enabled() else
                                   "off (embedded single-user mode; set DIP_AUTH=on to enforce)")

app = FastAPI(title="Dental Market Intelligence OS — Platform API", version=__version__,
              description="Intelligence Platform v2. See ARCHITECTURE_V2.md.")
app.add_middleware(AuditMiddleware)
# hardening (src/dip/api/security.py): rate limits, request-size limit, security headers on every response
app.add_middleware(RateLimitMiddleware)
app.add_middleware(BodySizeLimitMiddleware)
app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(CORSMiddleware, allow_origins=list(get_settings().cors_origins), allow_methods=["*"], allow_headers=["*"],
                   allow_credentials="*" not in get_settings().cors_origins)   # session cookie only for listed origins

for r in (operations.router, catalog.router, intelligence.router, people.router, auth_routes.router, enterprise.router, workflow.router, orgs.router, pilot.router, metrics.router, knowledge.router, acquire.router, sourcing_intel.router, watch.router, keywords.router, own_sales.router):
    app.include_router(r, prefix="/api/v2")
app.include_router(exports.router, prefix="/api/v2")  # Phase D: CSV/XLSX tables and the decision memo

