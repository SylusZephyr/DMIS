"""Provider plumbing shared by every live source: credentials, rate limits, retries, cache, raw archive, cost.

* **Credentials** come from the environment only (names in config/platform/acquisition.yaml); a provider
  without them is ``not configured`` and is never called.
* **Rate limit**: at most ``requests_per_minute`` per provider (per process); ``Retry-After`` is honoured.
* **Retries**: 429 / 5xx / timeouts are retried with exponential backoff (3 attempts), everything else fails
  at once with the provider's message.
* **Cache**: an identical request inside ``run.cache_hours`` is served from disk, so re-running a market does
  not pay twice.
* **Landing archive**: every response body is written once, unmodified, to
  ``<data_dir>/landing/<provider>/<date>/<hash>.<ext>`` -- the raw evidence behind every acquired number
  (the live counterpart of data/raw/). Secrets are removed from the recorded request.
* **Budget**: each request adds the provider's ``cost_usd_per_request`` to the run's ledger; a run that
  reaches ``run.budget_usd_per_run`` stops with ``BudgetExceeded`` and keeps what it already fetched.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import lru_cache
from typing import Any, Callable

import httpx
import yaml

from dip.settings import PROJECT_ROOT, get_settings

SECRET_KEYS = {"key", "api_key", "apikey", "access_key", "secret", "token", "sign", "signature", "app_secret",
               "_aop_signature", "access_token",
               "partnertag", "partner_tag", "x-amz-security-token", "authorization"}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "acquisition.yaml").read_text(encoding="utf-8"))


def env(name: str) -> str | None:
    v = os.environ.get(name, "").strip()
    return v or None


class AcquireError(RuntimeError):
    """A provider answered with an error (after retries) or an unusable body."""


class NotConfigured(AcquireError):
    pass


class BudgetExceeded(AcquireError):
    pass


@dataclass
class Ledger:
    """Requests, cost and errors of one acquisition run."""
    budget_usd: float = field(default_factory=lambda: float(config()["run"]["budget_usd_per_run"]))
    requests: int = 0
    cached: int = 0
    cost_usd: float = 0.0
    errors: list[str] = field(default_factory=list)
    by_provider: dict[str, dict] = field(default_factory=dict)
    landing: list[str] = field(default_factory=list)

    def charge(self, provider: str, cost: float, cached: bool) -> None:
        p = self.by_provider.setdefault(provider, {"requests": 0, "cached": 0, "cost_usd": 0.0})
        if cached:
            self.cached += 1
            p["cached"] += 1
            return
        if self.cost_usd + cost > self.budget_usd + 1e-9:
            raise BudgetExceeded(f"run budget ${self.budget_usd:.2f} reached after {self.requests} requests "
                                 f"(${self.cost_usd:.2f}); raise run.budget_usd_per_run to fetch more")
        self.requests += 1
        self.cost_usd = round(self.cost_usd + cost, 6)
        p["requests"] += 1
        p["cost_usd"] = round(p["cost_usd"] + cost, 6)

    def to_dict(self) -> dict:
        return {"requests": self.requests, "cached": self.cached, "cost_usd": round(self.cost_usd, 4),
                "budget_usd": self.budget_usd, "errors": self.errors[:50], "by_provider": self.by_provider,
                "landing_files": len(self.landing)}


_last_call: dict[str, float] = {}
_lock = threading.Lock()


def _redact(params: dict | None) -> dict:
    return {k: ("***" if k.lower() in SECRET_KEYS else v) for k, v in (params or {}).items()}


class HttpClient:
    """HTTP for one provider. ``transport`` and ``sleep`` are injectable so tests never touch the network."""

    def __init__(self, provider: str, ledger: Ledger | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep, timeout: float = 45.0, settings: dict | None = None):
        pc = settings if settings is not None else config()["providers"].get(provider, {})
        self.provider = provider
        self.ledger = ledger or Ledger()
        self.rpm = float(pc.get("requests_per_minute", 30))
        self.cost = float(pc.get("cost_usd_per_request", 0.0))
        self.sleep = sleep
        self.client = httpx.Client(transport=transport, timeout=timeout, follow_redirects=True)
        root = get_settings().data_dir
        self.cache_dir = root / "acquire_cache" / provider
        self.landing_dir = root / "landing" / provider
        self.cache_seconds = float(config()["run"]["cache_hours"]) * 3600

    # ---------------------------------------------------------------- helpers
    def _key(self, method: str, url: str, params: dict | None, body: Any) -> str:
        raw = json.dumps([method, url, sorted((params or {}).items()), body], sort_keys=True, default=str)
        return hashlib.sha256(raw.encode()).hexdigest()[:32]

    def _throttle(self, min_gap: float | None = None) -> None:
        gap = max(60.0 / max(self.rpm, 0.1), min_gap or 0.0)
        with _lock:
            wait = _last_call.get(self.provider, 0.0) + gap - time.monotonic()
        if wait > 0:
            self.sleep(wait)
        with _lock:
            _last_call[self.provider] = time.monotonic()

    def _archive(self, key: str, body: bytes, ext: str, meta: dict) -> str:
        day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        d = self.landing_dir / day
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"{key}.{ext}"
        if not p.exists():                         # immutable: the first observation is kept
            p.write_bytes(body)
            (d / f"{key}.meta.json").write_text(json.dumps(meta, default=str), encoding="utf-8")
        self.ledger.landing.append(str(p))
        return str(p)

    # ---------------------------------------------------------------- request
    def request(self, method: str, url: str, *, params: dict | None = None, json_body: Any = None,
                headers: dict | None = None, content: bytes | None = None, expect: str = "json",
                min_gap: float | None = None, cache: bool = True) -> Any:
        key = self._key(method, url, params, json_body if json_body is not None else (content or b"").hex()[:2000])
        cpath = self.cache_dir / f"{key}.{'json' if expect == 'json' else 'html'}"
        if cache and cpath.exists() and time.time() - cpath.stat().st_mtime < self.cache_seconds:
            self.ledger.charge(self.provider, 0.0, cached=True)
            raw = cpath.read_bytes()
            return json.loads(raw) if expect == "json" else raw.decode("utf-8", "replace")
        self.ledger.charge(self.provider, self.cost, cached=False)
        last: Exception | None = None
        for attempt in range(3):
            self._throttle(min_gap)
            try:
                r = self.client.request(method, url, params=params, json=json_body, headers=headers, content=content)
            except httpx.TransportError as exc:          # timeouts, connection errors
                last = exc
                self.sleep(2 ** attempt)
                continue
            if r.status_code == 429 or r.status_code >= 500:
                ra = r.headers.get("retry-after")
                self.sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2 ** (attempt + 1))
                last = AcquireError(f"{self.provider}: HTTP {r.status_code}")
                continue
            if r.status_code >= 400:
                raise AcquireError(f"{self.provider}: HTTP {r.status_code}: {r.text[:300]}")
            body = r.content
            meta = {"provider": self.provider, "method": method, "url": url, "params": _redact(params),
                    "status": r.status_code, "fetched_at": datetime.now(timezone.utc).isoformat()}
            self._archive(key, body, "json" if expect == "json" else "html", meta)
            if cache:
                self.cache_dir.mkdir(parents=True, exist_ok=True)
                cpath.write_bytes(body)
            if expect == "json":
                try:
                    return r.json()
                except ValueError as exc:
                    raise AcquireError(f"{self.provider}: response is not JSON") from exc
            return r.text
        msg = f"{self.provider}: gave up after 3 attempts ({last})"
        self.ledger.errors.append(msg)
        raise AcquireError(msg)

    def get_json(self, url: str, params: dict | None = None, **kw) -> Any:
        return self.request("GET", url, params=params, expect="json", **kw)

    def get_text(self, url: str, params: dict | None = None, **kw) -> str:
        return self.request("GET", url, params=params, expect="text", **kw)


class Provider:
    """A live source. Subclasses set ``name`` and ``capabilities`` and implement the matching methods."""
    name = "base"
    capabilities: tuple[str, ...] = ()

    def __init__(self, ledger: Ledger | None = None, transport: httpx.BaseTransport | None = None,
                 sleep: Callable[[float], None] = time.sleep):
        self.ledger = ledger or Ledger()
        self.http = HttpClient(self.name, self.ledger, transport=transport, sleep=sleep)

    # capabilities (a provider implements the ones it lists)
    def search(self, term: str, page: int = 0) -> list[str]:
        raise NotImplementedError

    def details(self, asins: list[str]) -> list:
        raise NotImplementedError

    def reviews(self, asin: str, limit: int = 30) -> list:
        raise NotImplementedError

    def products(self, asins: list[str]) -> list[dict]:
        raise NotImplementedError

    @classmethod
    def missing(cls) -> list[str]:
        return [e for e in config()["providers"].get(cls.name, {}).get("env", []) if not env(e)]

    @classmethod
    def configured(cls) -> bool:
        return not cls.missing()
