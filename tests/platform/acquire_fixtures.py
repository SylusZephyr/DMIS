"""Recorded-shape fixtures for the acquisition layer: synthetic Keepa products (documented product-object
layout), a vendor-style JSON API, and amazon.com HTML pages. No test touches the network."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import httpx

KEEPA_OFFSET = 21564000
NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)

TITLES = [
    "Denture Repair Kit Complete Home Denture Reline and Repair Set",
    "Soft Denture Reliner Kit for Upper and Lower Dentures",
    "Denture Base Resin Self Curing Acrylic Powder and Liquid",
    "Baseplate Wax Dental Pink Sheets for Denture Base",
    "Temporary Denture Tooth Replacement Kit Moldable Beads",
    "Hard Denture Reline Kit Professional Strength",
]


def kt(dt: datetime) -> int:
    return int(dt.timestamp() // 60) - KEEPA_OFFSET


def _hist(values: list[float], months_back: int) -> list[int]:
    """Flat Keepa history with one point per month, oldest first, ending now."""
    out: list[int] = []
    for i, v in enumerate(values):
        t = NOW - timedelta(days=30 * (months_back - i))
        out += [kt(t), int(v)]
    return out


LADDER = [50, 100, 200, 300, 500, 1000]


def keepa_product(i: int) -> dict:
    asin = f"B0TEST{i:04d}"
    months = 6
    base_price = 900 + 150 * (i % 7)                         # cents
    prices = [base_price + 20 * k for k in range(months)]
    rank = [20000 - 500 * k - 100 * i for k in range(months)]
    rating = [40 + (i % 6)] * months                         # 4.0 .. 4.5 (x10)
    counts = [50 + 10 * i + 5 * k for k in range(months)]
    badge = LADDER[i % len(LADDER)] if i % 4 != 3 else None  # a quarter of listings show no badge
    csv: list = [None] * 18
    csv[0] = None
    csv[1] = _hist(prices, months)
    csv[3] = _hist(rank, months)
    csv[16] = _hist(rating, months)
    csv[17] = _hist(counts, months)
    p = {"asin": asin, "title": f"{TITLES[i % len(TITLES)]} ({i})", "brand": f"Brand{i % 9}", "manufacturer": f"Maker{i % 5}",
         "csv": csv, "imagesCSV": f"img{i}.jpg,alt{i}.jpg",
         "categoryTree": [{"catId": 1, "name": "Health & Household"}, {"catId": 2, "name": "Denture Care"}],
         "listedSince": kt(NOW - timedelta(days=400 + 20 * i)), "packageWeight": 120 + 5 * i,
         "fbaFees": {"pickAndPackFee": 322 + i}}
    if badge:
        p["monthlySold"] = badge
        p["monthlySoldHistory"] = _hist([badge] * months, months)
    return p


PRODUCTS = [keepa_product(i) for i in range(36)]


def keepa_transport(products: list[dict] | None = None, calls: list | None = None, tokens_left: int = 500) -> httpx.MockTransport:
    products = products or PRODUCTS
    by_asin = {p["asin"]: p for p in products}

    def handler(req: httpx.Request) -> httpx.Response:
        if calls is not None:
            calls.append(str(req.url))
        q = dict(req.url.params)
        assert q.get("key"), "Keepa requests carry the key"
        if req.url.path.endswith("/search"):
            page = int(q.get("page", 0))
            asins = [p["asin"] for p in products][page * 12:(page + 1) * 12]
            return httpx.Response(200, json={"asinList": asins, "tokensLeft": tokens_left})
        if req.url.path.endswith("/product"):
            asins = q["asin"].split(",")
            return httpx.Response(200, json={"products": [by_asin[a] for a in asins if a in by_asin], "tokensLeft": tokens_left})
        return httpx.Response(404, json={"error": {"message": "unknown"}})
    return httpx.MockTransport(handler)


def scraper_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        q = dict(req.url.params)
        if q["type"] == "search":
            return httpx.Response(200, json={"request_info": {"success": True}, "search_results": [
                {"asin": "B0VEND0001", "title": "Denture Reline Kit Soft", "price": {"value": 14.99}, "rating": 4.2,
                 "ratings_total": 812, "recent_sales": "1K+ bought in past month", "link": "https://www.amazon.com/dp/B0VEND0001",
                 "image": "https://m.media-amazon.com/images/I/x.jpg"},
                {"asin": "B0VEND0002", "title": "Denture Repair Kit", "price": {"value": 9.49}, "rating": 3.9, "ratings_total": 97}]})
        if q["type"] == "product":
            return httpx.Response(200, json={"request_info": {"success": True}, "product": {
                "asin": q["asin"], "title": f"Product {q['asin']}", "brand": "Acme", "rating": 4.1, "ratings_total": 500,
                "buybox_winner": {"price": {"value": 12.5}}, "bestsellers_rank": [{"rank": 4321, "category": "Health"}],
                "categories": [{"name": "Health"}, {"name": "Denture Care"}], "weight": "4.8 ounces",
                "first_available": {"raw": "March 3, 2024"}}})
        if q["type"] == "reviews":
            if q.get("page") != "1":
                return httpx.Response(200, json={"request_info": {"success": True}, "reviews": []})
            return httpx.Response(200, json={"request_info": {"success": True}, "reviews": [
                {"id": "R1", "title": "Broke after a week", "body": "The reline material cracked after one week and smells bad.",
                 "rating": 2, "date": {"raw": "Reviewed in the United States on March 3, 2026"}, "verified_purchase": True,
                 "helpful_votes": 12},
                {"id": "R2", "title": "Works", "body": "Easy to use and fits well.", "rating": 5, "verified_purchase": False}]})
        return httpx.Response(400, text="bad")
    return httpx.MockTransport(handler)


SEARCH_HTML = """<html><body><div class="s-main-slot">
<div data-component-type="s-search-result" data-asin="B0HTML0001">
  <h2><a href="/dp/B0HTML0001"><span>Denture Repair Kit, Complete Reline Set</span></a></h2>
  <span class="a-icon-alt">4.4 out of 5 stars</span>
  <span aria-label="2,345 ratings">2,345</span>
  <span class="a-size-base a-color-secondary">1K+ bought in past month</span>
  <span class="a-price"><span class="a-offscreen">$19.99</span></span>
  <img class="s-image" src="https://m.media-amazon.com/images/I/a.jpg"/>
</div>
<div data-component-type="s-search-result" data-asin="B0HTML0002">
  <h2><span>Soft Denture Reliner</span></h2>
  <span class="a-icon-alt">3.8 out of 5 stars</span>
  <span class="a-price"><span class="a-offscreen">$8.50</span></span>
</div>
<div data-component-type="s-search-result" data-asin=""><h2><span>ad slot</span></h2></div>
</div></body></html>"""

PRODUCT_HTML = """<html><head><link rel="canonical" href="https://www.amazon.com/Denture-Repair/dp/B0HTML0001"/></head><body>
<div id="wayfinding-breadcrumbs_feature_div"><a>Health &amp; Household</a><a>Oral Care</a><a>Denture Care</a></div>
<span id="productTitle"> Denture Repair Kit, Complete Reline Set </span>
<a id="bylineInfo">Visit the DentaFix Store</a>
<span id="acrPopover" title="4.4 out of 5 stars"></span>
<span id="acrCustomerReviewText">2,345 ratings</span>
<span id="social-proofing-faceout-title-tk_bought">1K+ bought in past month</span>
<div id="corePrice_feature_div"><span class="a-offscreen">$19.99</span></div>
<img id="landingImage" data-old-hires="https://m.media-amazon.com/images/I/big.jpg" src="small.jpg"/>
<div id="detailBullets_feature_div"><ul>
 <li><span>Package Dimensions &#x200f; : &#x200e;</span> <span>5 x 3 x 2 inches; 4.8 Ounces</span></li>
 <li><span>Date First Available &#x200f; : &#x200e;</span> <span>March 3, 2024</span></li>
 <li><span>Best Sellers Rank:</span> <span>#1,234 in Health &amp; Household</span></li>
</ul></div>
<div data-hook="review" id="RHTML1">
  <i data-hook="review-star-rating"><span>2.0 out of 5 stars</span></i>
  <a data-hook="review-title"><span>2.0 out of 5 stars</span><span>Cracked quickly</span></a>
  <span data-hook="review-date">Reviewed in the United States on March 3, 2026</span>
  <span data-hook="avp-badge">Verified Purchase</span>
  <span data-hook="review-body"><span>The base cracked after two days and the liquid smells terrible.</span></span>
  <span data-hook="helpful-vote-statement">12 people found this helpful</span>
</div>
<div data-hook="review" id="RHTML2">
  <i data-hook="review-star-rating"><span>5.0 out of 5 stars</span></i>
  <a data-hook="review-title"><span>Great fit</span></a>
  <span data-hook="review-body"><span>Fits well, easy to mix.</span></span>
</div>
</body></html>"""

CAPTCHA_HTML = "<html><body><form action='/errors/validateCaptcha'>Enter the characters you see below</form></body></html>"


def html_transport() -> httpx.MockTransport:
    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/s":
            return httpx.Response(200, text=SEARCH_HTML)
        if req.url.path.startswith("/dp/"):
            return httpx.Response(200, text=PRODUCT_HTML.replace("B0HTML0001", req.url.path.split("/")[-1]))
        return httpx.Response(404, text="")
    return httpx.MockTransport(handler)


def dump(o) -> str:
    return json.dumps(o)


def combined_transport(calls: list | None = None) -> httpx.MockTransport:
    """Keepa for search/details/history and the vendor API for reviews, routed by host."""
    k, s = keepa_transport(calls=calls), scraper_transport()

    def handler(req: httpx.Request) -> httpx.Response:
        return (k if req.url.host == "api.keepa.com" else s).handle_request(req)
    return httpx.MockTransport(handler)
