"""Audit log: who did what, when, to which resource.

* Every state-changing API request (POST/PUT/PATCH/DELETE under /api/v2) is recorded by
  ``AuditMiddleware`` with the caller, route, status and resource id.
* Important domain actions (project approval, stage change, user creation...) also call
  ``record`` with their own action name and detail.
Reading the log requires ``audit:read`` (admin).
"""

from __future__ import annotations

import logging
import re

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from dip.storage import business as b

log = logging.getLogger("dip.audit")
MUTATING = {"POST", "PUT", "PATCH", "DELETE"}
_RESOURCE = re.compile(r"^/api/v2/([a-z_]+)(?:/([^/]+))?")
# POST endpoints that only compute an answer or record page usage: they change no business state, so they are
# not audit events (page views go to the usage table; questions and simulations are not actions).
READ_ONLY_POSTS = re.compile(r"^/api/v2/(telemetry/view|analyst/ask(-v3)?|ask|launch/(simulate|compare(-v3)?|evaluate)|"
                             r"shopping/recommend(-v3)?|simulate)$")


def record(action: str, principal=None, resource: str | None = None, resource_id: str | None = None,
           detail: dict | None = None, status: int | None = None) -> None:
    try:
        with b.session() as s:
            s.add(b.AuditLog(action=action[:128], resource=resource, resource_id=(resource_id or "")[:255] or None,
                             user_id=getattr(principal, "user_id", None),
                             user=(getattr(principal, "email", None) or getattr(principal, "name", None)),
                             role=getattr(principal, "role", None), status=status, detail=detail))
    except Exception:  # auditing must never break the action itself
        log.exception("audit record failed")


def _principal_for(request: Request):
    from dip.auth import ANONYMOUS_LOCAL, hash_token, request_token

    token = request_token(request)   # bearer header, else the browser session cookie
    if not token:
        return ANONYMOUS_LOCAL
    with b.session() as s:
        t = s.query(b.ApiToken).filter(b.ApiToken.token_hash == hash_token(token)).first()
        u = s.get(b.User, t.user_id) if t else None
        if u is None:
            return None

        class P:  # minimal view for the log
            user_id, email, name, role = u.id, u.email, u.name, u.role
        return P


class AuditMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        path = request.url.path
        if request.method in MUTATING and path.startswith("/api/v2") and not path.endswith("/health") \
                and not READ_ONLY_POSTS.match(path):
            m = _RESOURCE.match(path)
            record(f"{request.method} {path}", _principal_for(request), m.group(1) if m else None,
                   m.group(2) if m else None, {"query": str(request.url.query) or None}, response.status_code)
        return response
