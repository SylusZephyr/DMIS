"""Caching and incremental processing.

* ``input_fingerprint`` -- everything a market's result depends on (file content,
  snapshot date, marketplace, reviews, human corrections, supplier list,
  configuration, code version). Re-processing with an identical fingerprint is
  skipped: the stored result is already exactly what a re-run would produce.
* ``RelevanceCache`` -- relevance scores per unique listing text, keyed by the
  relevance model's fingerprint (config + corrections). A new monthly snapshot
  re-scores only texts it has not seen; any new correction changes the
  fingerprint and starts a fresh cache.
* ``response_cache`` -- read-API responses keyed by path, query and the data
  version (latest market update, supplier / ownership / alert counts), so a
  cached answer can never outlive the data it was computed from.
"""

from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from functools import wraps
from pathlib import Path

import pandas as pd

from dip import __version__
from dip.settings import PROJECT_ROOT, get_settings

CONFIG_DIR = PROJECT_ROOT / "config"


def _sha(*parts) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p if isinstance(p, bytes) else str(p).encode("utf-8"))
        h.update(b"\x1f")
    return h.hexdigest()


def config_digest() -> str:
    files = sorted(p for p in CONFIG_DIR.rglob("*") if p.is_file() and p.suffix in (".yaml", ".yml", ".csv", ".json"))
    return _sha(*[p.relative_to(CONFIG_DIR).as_posix() + ":" + hashlib.sha256(p.read_bytes()).hexdigest() for p in files])


def feedback_digest(feedback: pd.DataFrame | None) -> str:
    if feedback is None or feedback.empty:
        return "none"
    cols = [c for c in ("record_key", "text", "label") if c in feedback]
    return _sha(pd.util.hash_pandas_object(feedback[cols].astype(str), index=False).sum())


SUPPLIER_INPUT_FIELDS = ("id", "name", "country", "city", "business_type", "oem", "odm", "certifications",
                         "product_categories")


def business_digest() -> str:
    """The business data a market's results depend on: the supplier list as entered (not the
    scores a run writes back). Ownership only routes alerts, so it is not part of it."""
    from dip.storage import business as b

    with b.session() as s:
        sup = s.query(b.Supplier).order_by(b.Supplier.id).all()
        return _sha(*[json.dumps([getattr(x, f) for f in SUPPLIER_INPUT_FIELDS], default=str) for x in sup])


def input_fingerprint(source, snapshot_date, marketplace, reviews: pd.DataFrame | None, feedback: pd.DataFrame | None,
                      overrides: dict | None) -> str | None:
    from dip.storage import lake

    if not isinstance(source, (str, Path)) or not Path(source).exists():
        return None                                     # API payloads / frames: always processed
    rv = "none" if reviews is None or reviews.empty else _sha(pd.util.hash_pandas_object(reviews.astype(str), index=False).sum())
    return _sha(lake.content_hash(source), snapshot_date, marketplace, rv, feedback_digest(feedback),
                json.dumps(overrides or {}, sort_keys=True, default=str), business_digest(), config_digest(), __version__)


# ------------------------------------------------------------------ relevance cache
class RelevanceCache:
    COLS = ["key", "relevance_score", "relevance_evidence", "relevance_status", "relevance_explanation"]

    def __init__(self, model_fingerprint: str):
        self.path = Path(get_settings().lake_dir) / "cache" / "relevance" / f"{model_fingerprint[:32]}.parquet"
        self._frame: pd.DataFrame | None = None

    def load(self) -> pd.DataFrame:
        if self._frame is None:
            self._frame = pd.read_parquet(self.path) if self.path.exists() else pd.DataFrame(columns=self.COLS)
        return self._frame

    def add(self, rows: pd.DataFrame) -> None:
        if rows.empty:
            return
        cur = self.load()
        self._frame = pd.concat([cur, rows[self.COLS]], ignore_index=True).drop_duplicates("key", keep="last")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        self._frame.to_parquet(tmp, index=False)
        tmp.replace(self.path)


def text_keys(keys: pd.Series) -> pd.Series:
    """Stable 16-byte hex digest per unique relevance text key."""
    return keys.map(lambda k: hashlib.blake2b(k.encode("utf-8"), digest_size=16).hexdigest())


# ------------------------------------------------------------------ API response cache
# Backend: in-process dict by default (tests, laptops: no Redis needed). With ``DIP_REDIS_URL`` set, a Redis
# server shared by every API worker/process. Redis errors never fail a request: the call falls back to the
# in-process store and Redis is retried after ``REDIS_RETRY_SECONDS``. Values are JSON (never pickle), so a
# response that is not JSON-serializable is only cached in-process.
log = logging.getLogger("dip.cache")
_lock = threading.Lock()
_store: dict[str, tuple[float, object]] = {}
MAX_ENTRIES = 512
TTL_SECONDS = 600
REDIS_PREFIX = "dmis:rc:"
REDIS_RETRY_SECONDS = 30.0


class MemoryBackend:
    name = "memory"

    def get(self, key: str):
        now = time.monotonic()
        with _lock:
            hit = _store.get(key)
        return hit[1] if hit and now - hit[0] < TTL_SECONDS else None

    def set(self, key: str, value) -> None:
        now = time.monotonic()
        with _lock:
            if len(_store) >= MAX_ENTRIES:
                for k in sorted(_store, key=lambda k: _store[k][0])[: MAX_ENTRIES // 4]:
                    _store.pop(k, None)
            _store[key] = (now, value)

    def clear(self) -> None:
        with _lock:
            _store.clear()


class RedisBackend:
    """Shared cache; ``client`` is a redis.Redis (or fakeredis) instance."""
    name = "redis"

    def __init__(self, client):
        self.client = client

    def get(self, key: str):
        raw = self.client.get(REDIS_PREFIX + key)
        return None if raw is None else json.loads(raw)

    def set(self, key: str, value) -> None:
        self.client.set(REDIS_PREFIX + key, json.dumps(value, allow_nan=False), ex=TTL_SECONDS)

    def clear(self) -> None:
        keys = list(self.client.scan_iter(match=REDIS_PREFIX + "*", count=500))
        for i in range(0, len(keys), 500):
            self.client.delete(*keys[i:i + 500])


_memory = MemoryBackend()
_redis: RedisBackend | None = None
_redis_url: str | None = None
_redis_down_until = 0.0


def _redis_client(url: str):
    import redis                                            # optional: pip install redis (platform-services extra)

    return redis.Redis.from_url(url, socket_connect_timeout=1.0, socket_timeout=1.0)


def backend():
    """The shared backend when configured and reachable, else the in-process one."""
    global _redis, _redis_url
    url = get_settings().redis_url
    if not url or time.monotonic() < _redis_down_until:
        return _memory
    if _redis is None or _redis_url != url:
        try:
            _redis, _redis_url = RedisBackend(_redis_client(url)), url
        except Exception as exc:                            # package missing or bad URL: stay in-process
            _redis_failed(exc)
            return _memory
    return _redis


def _redis_failed(exc: Exception) -> None:
    global _redis_down_until
    if time.monotonic() >= _redis_down_until:
        log.warning("response cache: Redis unavailable (%s); using the in-process cache for %.0fs", exc, REDIS_RETRY_SECONDS)
    _redis_down_until = time.monotonic() + REDIS_RETRY_SECONDS


def _cache_get(key: str):
    be = backend()
    try:
        return be.get(key)
    except Exception as exc:                                # connection / timeout / corrupt value
        _redis_failed(exc)
        return _memory.get(key)


def _cache_set(key: str, value) -> None:
    be = backend()
    try:
        be.set(key, value)
    except (TypeError, ValueError):                         # not JSON-serializable: keep it in-process only
        _memory.set(key, value)
    except Exception as exc:
        _redis_failed(exc)
        _memory.set(key, value)


def data_version() -> str:
    from sqlalchemy import func

    from dip.storage import business as b

    with b.session() as s:
        return "|".join(str(x) for x in (s.query(func.max(b.Market.updated_at)).scalar(), s.query(func.count(b.Market.name)).scalar(),
                                         s.query(func.count(b.Supplier.id)).scalar(), s.query(func.count(b.Ownership.id)).scalar(),
                                         s.query(func.count(b.Event.id)).scalar()))


def response_cache(fn):
    """Cache a read endpoint's result per (arguments, data version, market scope). Arguments must be hashable
    via repr; a ``principal`` argument contributes only its market scope."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        scope = None
        parts = []
        for k, v in sorted(kwargs.items()):
            if k == "principal":
                sc = v.market_scope()
                scope = None if sc is None else sorted(sc)
                continue
            parts.append(f"{k}={v!r}")
        key = _sha(fn.__module__, fn.__qualname__, *map(repr, args), *parts, json.dumps(scope), data_version())
        hit = _cache_get(key)
        if hit is not None:
            return hit
        out = fn(*args, **kwargs)
        if out is not None:
            _cache_set(key, out)
        return out
    return wrapper


def clear() -> None:
    _memory.clear()
    be = backend()
    if be is not _memory:
        try:
            be.clear()
        except Exception as exc:
            _redis_failed(exc)
