"""Amazon Product Advertising API 5.0 (associates): search and item details, signed with AWS SigV4.

Carries title, brand, price, image, sales rank, rating and rating count; no sales badge and no review text.
Needs an Associates account with API access (DIP_PAAPI_ACCESS_KEY / DIP_PAAPI_SECRET_KEY / DIP_PAAPI_PARTNER_TAG).
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone

from dip.acquire.base import AcquireError, Provider, config, env
from dip.acquire.models import Listing

SERVICE = "ProductAdvertisingAPI"
RESOURCES = ["ItemInfo.Title", "ItemInfo.ByLineInfo", "ItemInfo.ProductInfo", "Offers.Listings.Price", "Images.Primary.Large",
             "BrowseNodeInfo.WebsiteSalesRank", "BrowseNodeInfo.BrowseNodes", "CustomerReviews.Count", "CustomerReviews.StarRating"]


def _sign(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def sigv4_headers(host: str, region: str, path: str, target: str, body: str, access: str, secret: str,
                  now: datetime | None = None) -> dict:
    """Headers for a signed PA-API POST (AWS Signature Version 4)."""
    t = now or datetime.now(timezone.utc)
    amz_date, day = t.strftime("%Y%m%dT%H%M%SZ"), t.strftime("%Y%m%d")
    headers = {"content-encoding": "amz-1.0", "content-type": "application/json; charset=utf-8", "host": host,
               "x-amz-date": amz_date, "x-amz-target": target}
    signed = ";".join(sorted(headers))
    canonical = "\n".join(["POST", path, "", "".join(f"{k}:{headers[k]}\n" for k in sorted(headers)), signed,
                           hashlib.sha256(body.encode("utf-8")).hexdigest()])
    scope = f"{day}/{region}/{SERVICE}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode("utf-8")).hexdigest()])
    k = _sign(_sign(_sign(_sign(("AWS4" + secret).encode("utf-8"), day), region), SERVICE), "aws4_request")
    sig = hmac.new(k, to_sign.encode("utf-8"), hashlib.sha256).hexdigest()
    headers["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return headers


def _dv(d: dict | None, *path):
    cur = d
    for p in path:
        if isinstance(cur, list):
            cur = cur[0] if cur else None
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur


class PAAPI(Provider):
    name = "paapi"
    capabilities = ("amazon_search", "amazon_detail")

    def _post(self, op: str, payload: dict) -> dict:
        pc = config()["providers"]["paapi"]
        host, region = pc["host"], pc["region"]
        path = f"/paapi5/{op.lower()}"
        body = json.dumps({**payload, "PartnerTag": env("DIP_PAAPI_PARTNER_TAG"), "PartnerType": "Associates",
                           "Marketplace": "www.amazon.com", "Resources": RESOURCES}, separators=(",", ":"))
        headers = sigv4_headers(host, region, path, f"com.amazon.paapi5.v1.ProductAdvertisingAPIv1.{op}", body,
                                env("DIP_PAAPI_ACCESS_KEY") or "", env("DIP_PAAPI_SECRET_KEY") or "")
        data = self.http.request("POST", f"https://{host}{path}", content=body.encode("utf-8"), headers=headers, expect="json")
        if data.get("Errors"):
            raise AcquireError(f"paapi: {data['Errors'][0].get('Message')}")
        return data

    def search(self, term: str, page: int = 0) -> list[str]:
        data = self._post("SearchItems", {"Keywords": term, "ItemPage": page + 1, "ItemCount": 10})
        return [i["ASIN"] for i in _dv(data, "SearchResult", "Items") or [] if i.get("ASIN")]

    def details(self, asins: list[str]) -> list[Listing]:
        out = []
        for i in range(0, len(asins), 10):
            data = self._post("GetItems", {"ItemIds": asins[i:i + 10]})
            out += [self.listing(it) for it in (_dv(data, "ItemsResult", "Items") or [])]
        return out

    @staticmethod
    def listing(it: dict) -> Listing:
        w = _dv(it, "ItemInfo", "ProductInfo", "ItemDimensions", "Weight")
        grams = None
        if isinstance(w, dict) and w.get("DisplayValue") is not None:
            unit = str(w.get("Unit", "")).lower()
            grams = round(float(w["DisplayValue"]) * {"pounds": 453.592, "ounces": 28.3495, "kilograms": 1000.0, "grams": 1.0}.get(unit, 0), 1) or None
        nodes = _dv(it, "BrowseNodeInfo", "BrowseNodes") or []
        return Listing(asin=it["ASIN"], title=_dv(it, "ItemInfo", "Title", "DisplayValue"),
                       brand=_dv(it, "ItemInfo", "ByLineInfo", "Brand", "DisplayValue"),
                       price=_dv(it, "Offers", "Listings", "Price", "Amount"),
                       rating=_dv(it, "CustomerReviews", "StarRating", "Value"), reviews=_dv(it, "CustomerReviews", "Count"),
                       image=_dv(it, "Images", "Primary", "Large", "URL"), url=it.get("DetailPageURL"),
                       category=nodes[0].get("DisplayName") if nodes and isinstance(nodes[0], dict) else None,
                       bsr=_dv(it, "BrowseNodeInfo", "WebsiteSalesRank", "SalesRank"), package_weight_g=grams, source="paapi")
