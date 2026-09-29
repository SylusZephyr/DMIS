"""Authentication and permission enforcement for /api/v2.

* Bearer API tokens (``Authorization: Bearer dmis_...``); only the SHA-256 of a
  token is stored, the token itself is shown once when created.
* Browser sessions: ``POST /auth/session`` exchanges a token for an httpOnly,
  SameSite=Lax cookie (``dmis_session``) so the web app need not keep the token in
  script-readable storage. A bearer header always takes precedence over the cookie;
  state-changing requests authenticated only by the cookie must come from an allowed origin.
* Every route declares ``require(resource, action)``; a user's role grants
  actions per resource through the ``permissions`` table
  (read < write < admin; role ``admin`` has ``*``).
* Roles: admin (everything), manager, analyst, viewer, **product_manager** (read/write
  only the markets of the categories assigned to their employee record) and
  **customer** (shopping mode only). Market scoping is applied by ``require`` to any
  route that names a market (path or query) and by ``visible_markets`` to lists.
* Enforcement is ON when ``DIP_AUTH=on`` or, by default, whenever the platform
  runs against PostgreSQL (server mode). It is OFF by default for the
  embedded single-user laptop mode, and can be forced either way.
"""

from __future__ import annotations

import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, HTTPException, Request

from dip.settings import get_settings
from dip.storage import business as b

LEVEL = {"read": 1, "write": 2, "admin": 3}
ROLES = ("admin", "manager", "product_manager", "supplier_manager", "analyst", "viewer", "customer")
SCOPED_ROLES = {"product_manager"}
TOKEN_PREFIX = "dmis_"
SESSION_COOKIE = "dmis_session"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def auth_enabled() -> bool:
    v = os.environ.get("DIP_AUTH", "").strip().lower()
    if v in ("on", "true", "1", "yes"):
        return True
    if v in ("off", "false", "0", "no"):
        return False
    return bool(get_settings().postgres_url)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_token(user_id: str, name: str = "default", ttl_days: int | None = -1) -> str:
    """ttl_days: -1 = plan config default (config/platform/plans.yaml token_ttl_days), None = never expires."""
    from datetime import timedelta

    from dip.tenancy import plans_config

    if ttl_days == -1:
        ttl_days = plans_config().get("token_ttl_days")
    token = TOKEN_PREFIX + secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(days=ttl_days) if ttl_days else None
    with b.session() as s:
        s.add(b.ApiToken(user_id=user_id, name=name, token_hash=hash_token(token), expires_at=expires))
    return token


def create_user(email: str, name: str, role: str = "analyst", employee_id: str | None = None,
                org_id: str | None = None) -> tuple[str, str]:
    """Create (or update the role of) a user and issue a token. Returns (user_id, token)."""
    if role not in ROLES:
        raise ValueError(f"unknown role '{role}'")
    with b.session() as s:
        u = s.query(b.User).filter(b.User.email == email.strip().lower()).first()
        if u is None:
            u = b.User(email=email.strip().lower(), name=name, role=role, employee_id=employee_id, org_id=org_id)
            s.add(u)
            s.flush()
        else:
            u.role, u.name = role, name
            if employee_id:
                u.employee_id = employee_id
        uid = u.id
    return uid, issue_token(uid, "created with user")


@dataclass
class Principal:
    user_id: str | None
    email: str | None
    name: str
    role: str
    authenticated: bool
    employee_id: str | None = None
    org_id: str | None = None

    def market_scope(self) -> set[str] | None:
        """None = every market; a set = only these. Signed-in users see their organization's markets
        (once a second organization exists); product managers only their categories' markets within it."""
        if not self.authenticated:
            return None
        from dip import tenancy

        scope: set[str] | None = tenancy.org_markets(self.org_id) if tenancy.multi_tenant() else None
        if self.role not in SCOPED_ROLES:
            return scope
        if not self.employee_id:
            return set()
        with b.session() as s:
            rows = (s.query(b.Category.market_name).join(b.Ownership, b.Ownership.category_id == b.Category.id)
                    .filter(b.Ownership.employee_id == self.employee_id, b.Category.market_name.isnot(None)).all())
        own = {r[0] for r in rows}
        return own if scope is None else own & scope

    @property
    def org(self) -> str:
        return self.org_id or b.DEFAULT_ORG

    def may_see_market(self, market: str | None) -> bool:
        scope = self.market_scope()
        return scope is None or market is None or market in scope

    def can(self, resource: str, action: str) -> bool:
        if not self.authenticated:
            return not auth_enabled()
        with b.session() as s:
            perms = s.query(b.Permission).filter(b.Permission.role == self.role).all()
        need = LEVEL[action]
        return any(p.resource in ("*", resource) and LEVEL.get(p.action, 0) >= need for p in perms)


ANONYMOUS_LOCAL = Principal(None, None, "local user", "admin", False)


def bearer_token(request: Request) -> str:
    header = request.headers.get("authorization", "")
    return header[7:].strip() if header.lower().startswith("bearer ") else ""


def request_token(request: Request) -> str:
    """The bearer token, else the session cookie's token ('' when neither)."""
    return bearer_token(request) or (request.cookies.get(SESSION_COOKIE) or "").strip()


def _origin_allowed(request: Request) -> bool:
    """CSRF guard for cookie-authenticated writes: a browser sends Origin (or Referer) cross-site;
    it must be this host or a configured CORS origin. Absent both (non-browser client) is allowed."""
    origin = request.headers.get("origin") or ""
    if not origin:
        ref = request.headers.get("referer") or ""
        origin = "/".join(ref.split("/")[:3]) if ref else ""
    if not origin:
        return True
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or ""
    allowed = {o.rstrip("/") for o in get_settings().cors_origins}
    return origin.split("://", 1)[-1].rstrip("/") == host or origin.rstrip("/") in allowed


def current_principal(request: Request) -> Principal:
    token = bearer_token(request)
    from_cookie = False
    if not token:
        token = (request.cookies.get(SESSION_COOKIE) or "").strip()
        from_cookie = bool(token)
        if from_cookie and request.method not in SAFE_METHODS and not _origin_allowed(request):
            raise HTTPException(403, "cross-site request refused (session cookie used from another origin)")
    if not token:
        if auth_enabled():
            raise HTTPException(401, "authentication required: send 'Authorization: Bearer <token>'",
                                headers={"WWW-Authenticate": "Bearer"})
        return ANONYMOUS_LOCAL
    try:
        return principal_for_token(token)
    except HTTPException:
        if from_cookie and not auth_enabled():   # a stale cookie never locks out embedded single-user mode
            return ANONYMOUS_LOCAL
        raise


def principal_for_token(token: str) -> Principal:
    """The signed-in user of a token; 401 when it is unknown, revoked or expired."""
    with b.session() as s:
        t = s.query(b.ApiToken).filter(b.ApiToken.token_hash == hash_token(token), b.ApiToken.revoked.is_(False)).first()
        if t is None:
            raise HTTPException(401, "invalid or revoked token", headers={"WWW-Authenticate": "Bearer"})
        if t.expires_at is not None:
            exp = t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)
            if exp < datetime.now(timezone.utc):
                raise HTTPException(401, "token expired; create a new one", headers={"WWW-Authenticate": "Bearer"})
        u = s.get(b.User, t.user_id)
        t.last_used_at = datetime.now(timezone.utc)
        return Principal(u.id, u.email, u.name, u.role, True, u.employee_id, u.org_id)


def token_expiry(token: str) -> datetime | None:
    with b.session() as s:
        t = s.query(b.ApiToken).filter(b.ApiToken.token_hash == hash_token(token)).first()
        if t is None or t.expires_at is None:
            return None
        return t.expires_at if t.expires_at.tzinfo else t.expires_at.replace(tzinfo=timezone.utc)


def require(resource: str, action: str = "read"):
    def dep(request: Request, p: Principal = Depends(current_principal)) -> Principal:
        if not p.authenticated and not auth_enabled():
            return p
        if not p.can(resource, action):
            raise HTTPException(403, f"role '{p.role}' may not {action} {resource}")
        market = request.path_params.get("market") or request.query_params.get("market")
        if market and not p.may_see_market(market):
            raise HTTPException(403, f"market '{market}' is not assigned to you")
        return p
    return dep


def visible_markets(p: Principal, names):
    """Filter market names (or dicts/rows with a 'name'/'market' key) to the principal's scope."""
    scope = p.market_scope()
    if scope is None:
        return list(names)
    out = []
    for n in names:
        key = n.get("name", n.get("market")) if isinstance(n, dict) else n
        if key in scope:
            out.append(n)
    return out


def assert_market(p: Principal, market: str | None) -> None:
    if not p.may_see_market(market):
        raise HTTPException(403, f"market '{market}' is not assigned to you")
