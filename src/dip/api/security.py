"""HTTP hardening for the platform API: security headers, request-size limit, rate limits.

Settings (environment):
* ``DIP_MAX_UPLOAD_MB``        largest accepted request / uploaded file (default 200)
* ``DIP_MAX_UPLOAD_ROWS``      largest accepted dataset in rows (default 5,000,000)
* ``DIP_RATE_LIMIT``           ``off`` disables rate limiting (default on)
* ``DIP_RATE_LIMIT_AUTH``      sign-in / token requests per minute per client and endpoint (default 20)
* ``DIP_RATE_LIMIT_EXPENSIVE`` simulations / analyst questions per minute per client and endpoint (default 60)

Rate limits are in-process (one API process = one budget); a client is its token when it sends
one, else its IP address (``request.client``; behind a reverse proxy set ``DIP_TRUST_PROXY=on``
to use the last ``X-Forwarded-For`` hop instead).
"""

from __future__ import annotations

import hashlib
import os
import threading
import time
from collections import deque

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


def _env_num(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        return default


def max_upload_bytes() -> int:
    return int(_env_num("DIP_MAX_UPLOAD_MB", 200) * 1024 * 1024)


def max_upload_rows() -> int:
    return int(_env_num("DIP_MAX_UPLOAD_ROWS", 5_000_000))


# ------------------------------------------------------------------ headers
API_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
}
API_CSP = "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
_DOC_PATHS = ("/docs", "/redoc", "/openapi.json")   # Swagger UI loads its own scripts and styles


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        response = await call_next(request)
        for k, v in API_HEADERS.items():
            response.headers.setdefault(k, v)
        if not request.url.path.startswith(_DOC_PATHS):
            response.headers.setdefault("Content-Security-Policy", API_CSP)
        return response


# ------------------------------------------------------------------ request size
class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Refuse a request whose declared Content-Length exceeds the upload limit before it is read.
    (Chunked bodies without a length are bounded by the upload handler, which counts bytes.)"""

    async def dispatch(self, request: Request, call_next):
        length = request.headers.get("content-length")
        if length and length.isdigit() and int(length) > max_upload_bytes() + 1024 * 1024:  # + multipart overhead
            return JSONResponse({"detail": f"request too large: the limit is {max_upload_bytes() // 2**20} MB "
                                           f"(DIP_MAX_UPLOAD_MB)"}, status_code=413)
        return await call_next(request)


# ------------------------------------------------------------------ rate limits
AUTH_PATHS = {("POST", "/api/v2/auth/tokens"), ("POST", "/api/v2/auth/session")}
EXPENSIVE_PATHS = {("POST", "/api/v2/launch/simulate"), ("POST", "/api/v2/launch/compare-v3"),
                   ("POST", "/api/v2/analyst/ask-v3")}
WINDOW_SECONDS = 60.0


class RateLimiter:
    """Sliding one-minute window per (bucket, client)."""

    def __init__(self):
        self._hits: dict[tuple[str, str], deque] = {}
        self._lock = threading.Lock()

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()

    def hit(self, bucket: str, client: str, limit: int, now: float | None = None) -> float | None:
        """None when allowed; else seconds until the next request is allowed."""
        now = time.monotonic() if now is None else now
        with self._lock:
            q = self._hits.setdefault((bucket, client), deque())
            while q and q[0] <= now - WINDOW_SECONDS:
                q.popleft()
            if len(q) >= limit:
                return max(q[0] + WINDOW_SECONDS - now, 0.0)
            q.append(now)
            if len(self._hits) > 10000:   # bound memory: drop idle clients
                for k in [k for k, v in self._hits.items() if not v or v[-1] <= now - WINDOW_SECONDS]:
                    del self._hits[k]
            return None


limiter = RateLimiter()


def rate_limit_enabled() -> bool:
    return os.environ.get("DIP_RATE_LIMIT", "on").strip().lower() not in ("off", "0", "false", "no")


def client_key(request: Request) -> str:
    from dip.auth import request_token

    token = request_token(request)
    if token:
        return "t:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:24]
    if os.environ.get("DIP_TRUST_PROXY", "").strip().lower() in ("on", "1", "true", "yes"):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return "ip:" + fwd.split(",")[-1].strip()
    return "ip:" + (request.client.host if request.client else "unknown")


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        key = (request.method, request.url.path.rstrip("/"))
        bucket = "auth" if key in AUTH_PATHS else "expensive" if key in EXPENSIVE_PATHS else None
        if bucket and rate_limit_enabled():
            limit = int(_env_num("DIP_RATE_LIMIT_AUTH", 20) if bucket == "auth" else _env_num("DIP_RATE_LIMIT_EXPENSIVE", 60))
            wait = limiter.hit(f"{bucket}:{key[1]}", client_key(request), max(limit, 1))   # budget per endpoint
            if wait is not None:
                return JSONResponse({"detail": f"too many requests: at most {limit} per minute; retry in {int(wait) + 1} s"},
                                    status_code=429, headers={"Retry-After": str(int(wait) + 1)})
        return await call_next(request)
