"""Chinese marketplace platforms as sources of supplier offers.

* ``ali1688``   the official 1688 cross-border open API (keyword and image search); requests signed with
                ``_aop_signature`` = upper hex HMAC-SHA1(url_path + sorted(key + value), app_secret)
* ``aliexpress`` the official AliExpress affiliate API (``aliexpress.affiliate.product.query``); ``sign`` =
                upper hex HMAC-SHA256(sorted(key + value), app_secret)
* ``aggregator`` any third-party data API for 1688 / Taobao / Alibaba.com / Made-in-China that answers a
                keyword search with JSON (endpoint + parameter template from config)

Responses are mapped to ``Offer`` through the field lists in config/platform/sourcing_intel.yaml, so a platform
whose API names things differently is a configuration change. Prices are converted to USD with the configured,
dated rates -- never guessed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

import yaml

from dip.acquire.base import AcquireError, HttpClient, Ledger, env
from dip.settings import PROJECT_ROOT


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((PROJECT_ROOT / "config" / "platform" / "sourcing_intel.yaml").read_text(encoding="utf-8"))


@dataclass
class PriceTier:
    min_qty: int
    price: float            # unit price in the offer currency


@dataclass
class Offer:
    platform: str
    offer_id: str
    title: str
    title_en: str | None = None
    url: str | None = None
    image: str | None = None
    currency: str = "CNY"
    price: float | None = None               # lowest shown unit price, offer currency
    tiers: list[PriceTier] = field(default_factory=list)
    moq: int | None = None
    sold: int | None = None                  # recent sales / orders the platform shows
    repurchase_rate: float | None = None
    supplier: str | None = None
    supplier_id: str | None = None
    location: str | None = None
    years: float | None = None
    verified: bool | None = None             # factory / audited / verified supplier
    trade_assurance: bool | None = None
    rating: float | None = None              # 0-5 (converted when the platform uses 0-100 or a percentage)
    response_rate: float | None = None
    certifications: list[str] = field(default_factory=list)
    weight_g: float | None = None
    query: str | None = None
    raw: dict = field(default_factory=dict)

    def usd(self, value: float | None) -> float | None:
        if value is None:
            return None
        rate = config()["fx"]["rates_to_usd"].get(self.currency)
        if rate is None:
            raise AcquireError(f"no exchange rate for {self.currency}: set fx.rates_to_usd in sourcing_intel.yaml")
        return round(float(value) * float(rate), 4)

    def unit_price_at(self, qty: int) -> float | None:
        """Offer-currency unit price for an order of ``qty`` (the tier whose minimum it reaches)."""
        if self.tiers:
            ok = [t for t in sorted(self.tiers, key=lambda t: t.min_qty) if qty >= t.min_qty]
            return (ok[-1] if ok else sorted(self.tiers, key=lambda t: t.min_qty)[0]).price
        return self.price


# ---------------------------------------------------------------- field mapping
def pick(d: Any, paths: list[str] | str | None, default=None):
    for p in ([paths] if isinstance(paths, str) else (paths or [])):
        cur: Any = d
        for part in p.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, "", []):
            return cur
    return default


_NUM = re.compile(r"-?\d+(?:\.\d+)?")


def num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    m = _NUM.search(str(v).replace(",", ""))
    return float(m.group(0)) if m else None


def truthy(v) -> bool | None:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "是")


def tiers(v) -> list[PriceTier]:
    """[{startQuantity, price}] / [{"min": 2, "price": ..}] / "2-99: 3.5; 100+: 3.1" -> PriceTier list."""
    out: list[PriceTier] = []
    if isinstance(v, list):
        for t in v:
            if isinstance(t, dict):
                q = num(pick(t, ["startQuantity", "min_qty", "min", "quantity", "begin"]))
                p = num(pick(t, ["price", "value"]))
                if q is not None and p is not None:
                    out.append(PriceTier(int(q), p))
    elif isinstance(v, str):
        for part in re.split(r"[;,]", v):
            m = re.match(r"\s*(\d+)\D+?(\d+(?:\.\d+)?)\s*$", part.replace(":", " "))
            if m:
                out.append(PriceTier(int(m.group(1)), float(m.group(2))))
    return sorted(out, key=lambda t: t.min_qty)


def to_offer(platform: str, item: dict, query: str | None = None) -> Offer | None:
    pc = config()["platforms"][platform]
    f = pc["fields"]
    oid = pick(item, f.get("id"))
    title = pick(item, f.get("title"))
    if oid is None or not title:
        return None
    rating = num(pick(item, f.get("rating")))
    if rating is not None and rating > 5:                  # 0-100 or percentage scales -> 0-5
        rating = round(rating / 20.0, 2) if rating <= 100 else None
    rr = num(pick(item, f.get("repurchase_rate")))
    resp = num(pick(item, f.get("response_rate")))
    certs = pick(item, f.get("certifications"), [])
    if isinstance(certs, str):
        certs = [c.strip() for c in re.split(r"[,;/|]", certs) if c.strip()]
    elif isinstance(certs, list):
        certs = [str(c.get("name") if isinstance(c, dict) else c) for c in certs]
    tt = tiers(pick(item, f.get("price_tiers")))
    price = num(pick(item, f.get("price")))
    if price is None and tt:
        price = min(t.price for t in tt)
    return Offer(platform=platform, offer_id=str(oid), title=str(title), title_en=pick(item, f.get("title_en")),
                 url=pick(item, f.get("url")), image=pick(item, f.get("image")), currency=pc.get("currency", "CNY"),
                 price=price, tiers=tt, moq=int(m) if (m := num(pick(item, f.get("moq")))) else None,
                 sold=int(s) if (s := num(pick(item, f.get("sold")))) is not None else None,
                 repurchase_rate=(rr / 100 if rr is not None and rr > 1 else rr),
                 supplier=pick(item, f.get("supplier")), supplier_id=str(pick(item, f.get("supplier_id"), "")) or None,
                 location=pick(item, f.get("location")), years=num(pick(item, f.get("years"))),
                 verified=truthy(pick(item, f.get("verified"))), trade_assurance=truthy(pick(item, f.get("trade_assurance"))),
                 rating=rating, response_rate=(resp / 100 if resp is not None and resp > 1 else resp),
                 certifications=[str(c) for c in certs], weight_g=num(pick(item, f.get("weight_g"))),
                 query=query, raw={k: v for k, v in item.items() if not isinstance(v, (dict, list))})


# ---------------------------------------------------------------- platforms
class Platform:
    kind = "base"

    def __init__(self, name: str, ledger: Ledger | None = None, **http_kw):
        self.name = name
        self.pc = config()["platforms"][name]
        self.ledger = ledger or Ledger(budget_usd=float(config()["run"]["budget_usd_per_run"]))
        self.http = HttpClient(f"sourcing_{name}", self.ledger, settings=self.pc, **http_kw)

    def missing(self) -> list[str]:
        return [e for e in self.pc.get("env", []) if not env(e)]

    def search(self, query: str, page: int = 1) -> list[Offer]:
        raise NotImplementedError

    def _items(self, data: dict, query: str) -> list[Offer]:
        items = pick(data, self.pc["fields"]["items"], [])
        if isinstance(items, dict):
            items = [items]
        return [o for o in (to_offer(self.name, it, query) for it in items if isinstance(it, dict)) if o is not None]


def sign_1688(url_path: str, params: dict, secret: str) -> str:
    s = url_path + "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode("utf-8"), s.encode("utf-8"), hashlib.sha1).hexdigest().upper()


def sign_aliexpress(params: dict, secret: str) -> str:
    s = "".join(f"{k}{params[k]}" for k in sorted(params))
    return hmac.new(secret.encode("utf-8"), s.encode("utf-8"), hashlib.sha256).hexdigest().upper()


class Ali1688(Platform):
    kind = "ali1688"

    def _call(self, api: str, param_name: str, payload: dict) -> dict:
        key, secret = env(self.pc["env"][0]) or "", env(self.pc["env"][1]) or ""
        url_path = f"{api}/{key}"
        params = {param_name: json.dumps(payload, ensure_ascii=False, separators=(",", ":"))}
        tok = env(self.pc.get("token_env", "")) if self.pc.get("token_env") else None
        if tok:
            params["access_token"] = tok
        params["_aop_timestamp"] = str(int(time.time() * 1000))
        params["_aop_signature"] = sign_1688(url_path, params, secret)
        data = self.http.request("POST", f"{self.pc['gateway'].rstrip('/')}/{url_path}", params=params, expect="json")
        err = pick(data, ["error_message", "errorMessage", "result.message"]) if pick(data, ["result.success"]) is False or \
            pick(data, ["error_code", "errorCode"]) else None
        if err:
            raise AcquireError(f"1688: {err}")
        return data

    def search(self, query: str, page: int = 1) -> list[Offer]:
        data = self._call(self.pc["api"], "offerQueryParam",
                          {"keyword": query, "beginPage": page, "pageSize": int(self.pc.get("page_size", 50)), "country": "en"})
        return self._items(data, query)

    def image_search(self, image_url: str, page: int = 1) -> list[Offer]:
        data = self._call(self.pc["image_api"], "offerQueryParam",
                          {"imageAddress": image_url, "beginPage": page, "pageSize": int(self.pc.get("page_size", 50)), "country": "en"})
        return self._items(data, f"image:{image_url}")


class AliExpress(Platform):
    kind = "aliexpress"

    def search(self, query: str, page: int = 1) -> list[Offer]:
        key, secret, track = (env(e) or "" for e in self.pc["env"])
        params = {"app_key": key, "method": self.pc["method"], "sign_method": "sha256", "timestamp": str(int(time.time() * 1000)),
                  "keywords": query, "page_no": str(page), "page_size": str(self.pc.get("page_size", 50)),
                  "target_currency": "USD", "target_language": "EN", "tracking_id": track, "ship_to_country": "US"}
        params["sign"] = sign_aliexpress(params, secret)
        data = self.http.get_json(self.pc["gateway"], params)
        if pick(data, ["error_response.msg"]):
            raise AcquireError(f"aliexpress: {pick(data, ['error_response.msg'])}")
        return self._items(data, query)


class Aggregator(Platform):
    kind = "aggregator"

    def search(self, query: str, page: int = 1) -> list[Offer]:
        key, url = env(self.pc["env"][0]) or "", env(self.pc["env"][1]) or ""
        params = {k: str(v).format(key=key, query=query, page=page) for k, v in self.pc["params"].items()}
        data = self.http.get_json(url, params)
        if isinstance(data, dict) and (data.get("error") or data.get("error_code")) and not pick(data, self.pc["fields"]["items"]):
            raise AcquireError(f"{self.name}: {data.get('error') or data.get('reason') or data.get('error_code')}")
        return self._items(data, query)


KINDS = {"ali1688": Ali1688, "aliexpress": AliExpress, "aggregator": Aggregator}


def make(name: str, ledger: Ledger | None = None, **http_kw) -> Platform:
    return KINDS[config()["platforms"][name]["kind"]](name, ledger, **http_kw)


def status() -> list[dict]:
    out = []
    for name, pc in config()["platforms"].items():
        missing = [e for e in pc.get("env", []) if not env(e)]
        out.append({"name": name, "label": pc.get("label", name), "kind": pc["kind"], "configured": not missing,
                    "missing": missing, "currency": pc.get("currency")})
    return out
