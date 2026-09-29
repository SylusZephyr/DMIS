"""Who am I, tokens, and user administration."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from dip import auth
from dip.api.util import clean
from dip.storage import business as b

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/me")
def me(p: auth.Principal = Depends(auth.current_principal)):
    scope = p.market_scope()
    return {"auth_enabled": auth.auth_enabled(), "authenticated": p.authenticated, "user_id": p.user_id,
            "email": p.email, "name": p.name, "role": p.role, "employee_id": p.employee_id, "org_id": p.org,
            "markets": None if scope is None else sorted(scope)}


class SessionIn(BaseModel):
    token: str | None = None     # else the request's bearer token


SESSION_MAX_AGE = 30 * 86400     # cookie lifetime when the token itself never expires


def _secure_cookie(request: Request) -> bool:
    v = os.environ.get("DIP_COOKIE_SECURE", "").strip().lower()
    if v in ("1", "true", "on", "yes"):
        return True
    if v in ("0", "false", "off", "no"):
        return False
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "").lower() == "https"


@router.post("/session")
def start_session(request: Request, response: Response, body: SessionIn | None = None):
    """Exchange an API token for an httpOnly, SameSite=Lax session cookie (browser sign-in).
    The token is never readable by page scripts afterwards; API clients keep using bearer tokens."""
    token = ((body.token if body else None) or auth.bearer_token(request)).strip()
    if not token:
        raise HTTPException(400, "send the token in the body ({\"token\": \"dmis_...\"}) or as a bearer header")
    p = auth.principal_for_token(token)            # 401 when invalid, revoked or expired
    exp = auth.token_expiry(token)
    max_age = SESSION_MAX_AGE if exp is None else max(int((exp - datetime.now(timezone.utc)).total_seconds()), 0)
    response.set_cookie(auth.SESSION_COOKIE, token, max_age=max_age, httponly=True, samesite="lax",
                        secure=_secure_cookie(request), path="/api")
    from dip import audit
    audit.record("session.start", p, "auth", p.user_id)
    return {"authenticated": True, "user_id": p.user_id, "email": p.email, "role": p.role,
            "expires_in_seconds": max_age}


@router.delete("/session")
def end_session(request: Request, response: Response):
    """Sign out of the browser session (clears the cookie; the token itself stays valid until revoked)."""
    response.delete_cookie(auth.SESSION_COOKIE, path="/api", httponly=True, samesite="lax",
                           secure=_secure_cookie(request))
    return {"authenticated": False}


class TokenIn(BaseModel):
    name: str = "default"


@router.post("/tokens")
def new_token(body: TokenIn, p: auth.Principal = Depends(auth.current_principal)):
    if not p.authenticated:
        raise HTTPException(401, "sign in with an existing token to create another")
    return {"token": auth.issue_token(p.user_id, body.name), "note": "shown once -- store it now"}


@router.get("/tokens")
def my_tokens(p: auth.Principal = Depends(auth.current_principal)):
    if not p.authenticated:
        return []
    with b.session() as s:
        return clean([{k: v for k, v in b.row_dict(x).items() if k != "token_hash"}
                      for x in s.query(b.ApiToken).filter(b.ApiToken.user_id == p.user_id).all()])


@router.post("/tokens/{token_id}/revoke")
def revoke_token(token_id: str, p: auth.Principal = Depends(auth.current_principal)):
    with b.session() as s:
        t = s.get(b.ApiToken, token_id)
        if t is None:
            raise HTTPException(404, "token not found")
        if t.user_id != p.user_id and not p.can("users", "admin"):
            raise HTTPException(403, "you can only revoke your own tokens")
        t.revoked = True
    from dip import audit
    audit.record("token.revoke", p, "tokens", token_id)
    return {"revoked": True}


class UserIn(BaseModel):
    email: str
    name: str
    role: str = "analyst"
    employee_id: str | None = None     # product managers see the markets of this employee's categories
    org_id: str | None = None          # platform admins may create users in another organization


@router.post("/users")
def create_user(u: UserIn, p: auth.Principal = Depends(auth.require("users", "admin"))):
    from dip import audit, tenancy

    org = u.org_id if (u.org_id and p.org == b.DEFAULT_ORG) else p.org_id   # only platform admins pick another org
    try:
        tenancy.check_new_user(org)
    except tenancy.LimitError as exc:
        raise HTTPException(402, str(exc)) from exc
    try:
        if u.employee_id:
            with b.session() as s:
                if s.get(b.Employee, u.employee_id) is None:
                    raise ValueError(f"employee '{u.employee_id}' not found")
        uid, token = auth.create_user(u.email, u.name, u.role, employee_id=u.employee_id, org_id=org)
        audit.record("user.create", p, "users", uid, {"role": u.role, "org": org})
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"user_id": uid, "token": token, "note": "shown once -- give it to the user"}


@router.get("/users")
def users(p: auth.Principal = Depends(auth.require("users", "admin"))):
    with b.session() as s:
        return clean([b.row_dict(u) for u in s.query(b.User).all()
                      if p.org == b.DEFAULT_ORG or (u.org_id or b.DEFAULT_ORG) == p.org])
