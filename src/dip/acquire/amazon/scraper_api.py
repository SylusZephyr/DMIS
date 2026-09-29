"""Commercial Amazon data APIs (search, product and review pages as JSON).

Several vendors share one convention -- ``?api_key=..&type=search|product|reviews&amazon_domain=..`` returning
``search_results`` / ``product`` / ``reviews`` -- and the request templates and top-level field names are in
config (``providers.scraper_api``), so another vendor is a configuration change. Field names inside the
objects vary slightly between vendors; ``_pick`` accepts the common spellings.
"""

from __future__ import annotations

import hashlib
from typing import Any

from dip.acquire.amazon import parse
from dip.acquire.base import AcquireError, Provider, config, env
from dip.acquire.models import Listing, Review


def _pick(d: dict | None, *keys, default=None):
    """First present key; dotted keys walk nested objects ('price.value')."""
    if not isinstance(d, dict):
        return default
    for k in keys:
        cur: Any = d
        for part in k.split("."):
            cur = cur.get(part) if isinstance(cur, dict) else None
            if cur is None:
                break
        if cur not in (None, "", []):
            return cur
    return default


class ScraperAPI(Provider):
    name = "scraper_api"
    capabilities = ("amazon_search", "amazon_detail", "amazon_reviews")

    @classmethod
    def missing(cls) -> list[str]:
        out = super().missing()
        url_env = config()["providers"]["scraper_api"]["url_env"]
        return out + ([url_env] if not env(url_env) else [])

    def _call(self, kind: str, **values) -> dict:
        pc = config()["providers"]["scraper_api"]
        params = {k: str(v).format(key=env(pc["env"][0]), **{k2: v2 for k2, v2 in values.items()})
                  for k, v in pc["params"][kind].items()}
        data = self.http.get_json(env(pc["url_env"]) or "", params)
        if not isinstance(data, dict):
            raise AcquireError("scraper_api: unexpected response")
        info = data.get("request_info") or {}
        if info.get("success") is False:
            raise AcquireError(f"scraper_api: {info.get('message') or 'request failed'}")
        return data

    # ---------------------------------------------------------------- capabilities
    def search_listings(self, term: str, page: int = 1) -> list[Listing]:
        data = self._call("search", term=term, page=page)
        rows = data.get(config()["providers"]["scraper_api"]["fields"]["search_results"]) or []
        return [x for x in (self.from_search(r) for r in rows) if x is not None]

    def search(self, term: str, page: int = 0) -> list[str]:
        return [x.asin for x in self.search_listings(term, page + 1)]

    def details(self, asins: list[str]) -> list[Listing]:
        out = []
        for a in asins:
            data = self._call("product", asin=a)
            p = data.get(config()["providers"]["scraper_api"]["fields"]["product"])
            if isinstance(p, dict):
                out.append(self.from_product(p))
        return out

    def reviews(self, asin: str, limit: int = 30) -> list[Review]:
        out: list[Review] = []
        page = 1
        while len(out) < limit and page <= 10:
            data = self._call("reviews", asin=asin, page=page)
            rows = data.get(config()["providers"]["scraper_api"]["fields"]["reviews"]) or []
            if not rows:
                break
            out += [r for r in (self.from_review(asin, x) for x in rows) if r is not None]
            page += 1
        return out[:limit]

    # ---------------------------------------------------------------- normalization
    @staticmethod
    def from_search(r: dict) -> Listing | None:
        asin = _pick(r, "asin")
        if not asin:
            return None
        return Listing(asin=str(asin), title=_pick(r, "title"), brand=_pick(r, "brand"),
                       price=parse.money(_pick(r, "price.value", "price.raw", "price")),
                       sales=parse.bought_past_month(_pick(r, "recent_sales", "bought_last_month", "sales_volume")),
                       rating=parse.rating(_pick(r, "rating")), reviews=parse.integer(_pick(r, "ratings_total", "reviews_count")),
                       image=_pick(r, "image", "thumbnail"), url=_pick(r, "link", "url"), source="scraper_api")

    @staticmethod
    def from_product(p: dict) -> Listing:
        bsr = _pick(p, "bestsellers_rank")
        cats = _pick(p, "categories") or []
        spec = {str(s.get("name", "")).lower(): s.get("value") for s in (_pick(p, "specifications") or []) if isinstance(s, dict)}
        weight = _pick(p, "weight", "shipping_weight") or spec.get("item weight") or spec.get("package dimensions")
        return Listing(
            asin=str(_pick(p, "asin")), title=_pick(p, "title"), brand=_pick(p, "brand"),
            price=parse.money(_pick(p, "buybox_winner.price.value", "price.value", "price")),
            sales=parse.bought_past_month(_pick(p, "recent_sales", "bought_last_month")),
            rating=parse.rating(_pick(p, "rating")), reviews=parse.integer(_pick(p, "ratings_total", "reviews_total")),
            image=_pick(p, "main_image.link", "main_image", "image"), url=_pick(p, "link"),
            category=(cats[-1].get("name") if cats and isinstance(cats[-1], dict) else None),
            seller=_pick(p, "buybox_winner.fulfillment.third_party_seller.name", "manufacturer"),
            launch_date=parse.any_date(_pick(p, "first_available.utc", "first_available.raw", "date_first_available")),
            bsr=parse.integer(bsr[0].get("rank")) if isinstance(bsr, list) and bsr and isinstance(bsr[0], dict) else None,
            package_weight_g=parse.weight_grams(str(weight)) if weight else None,
            source="scraper_api")

    @staticmethod
    def from_review(asin: str, r: dict) -> Review | None:
        text = _pick(r, "body", "text", "content")
        if not text:
            return None
        return Review(asin=asin, review_id=str(_pick(r, "id", "review_id", default=hashlib.sha1(str(text).encode()).hexdigest()[:16])),
                      rating=parse.rating(_pick(r, "rating")), title=_pick(r, "title"), text=str(text),
                      date=parse.any_date(_pick(r, "date.utc", "date.raw", "date")),
                      verified=bool(_pick(r, "verified_purchase", default=False)),
                      helpful=parse.integer(_pick(r, "helpful_votes")), source="scraper_api")
